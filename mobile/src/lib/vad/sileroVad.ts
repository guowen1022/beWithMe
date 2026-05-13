// Silero VAD v5 on device. Port of frontend/lib/vad.ts adapted for RN —
// we drive inference manually from the existing AudioRecorder Int16 frame
// stream instead of relying on @ricky0123/vad-web's browser-side state
// machine.
//
// Tuning matches frontend/lib/vad.ts:VAD_TUNING verbatim:
//   - positiveSpeechThreshold: 0.5
//   - negativeSpeechThreshold: 0.35
//   - redemptionFrames:        20  (640 ms silence ends a phrase)
//   - minSpeechFrames:         4   (128 ms floor — drops coughs/clicks)
//   - preSpeechPadFrames:      6   (192 ms pre-roll — rescues initials)
//
// Frame size is 512 samples @ 16 kHz (32 ms) — the rate AudioRecorderModule
// emits.
//
// onPhrase fires once per detected phrase with a file:// URI to a WAV file
// containing the buffered phrase audio (pre-roll + speech + redemption tail).
// The URI is consumable by runVoiceTurn → transcribe → ask → speak.

import { InferenceSession, Tensor } from "onnxruntime-react-native";
import { Asset } from "expo-asset";
import { File, Paths } from "expo-file-system";

const FRAME_SAMPLES = 512;
const SAMPLE_RATE = 16000;
const STATE_DIMS: [number, number, number] = [2, 1, 128];
const STATE_LEN = STATE_DIMS[0] * STATE_DIMS[1] * STATE_DIMS[2];

const POS_THRESHOLD = 0.5;
const NEG_THRESHOLD = 0.35;
const REDEMPTION_FRAMES = 20;
const MIN_SPEECH_FRAMES = 4;
const PRE_SPEECH_PAD_FRAMES = 6;

export const VAD_TUNING = {
  positiveSpeechThreshold: POS_THRESHOLD,
  negativeSpeechThreshold: NEG_THRESHOLD,
  redemptionFrames: REDEMPTION_FRAMES,
  minSpeechFrames: MIN_SPEECH_FRAMES,
  preSpeechPadFrames: PRE_SPEECH_PAD_FRAMES,
} as const;

export { FRAME_SAMPLES, SAMPLE_RATE };

export interface SileroVadHandle {
  pushFrame(frame: Int16Array): void;
  /** Force-finalize the in-flight phrase (PTT-style release). No-op outside speech. */
  flush(): void;
  destroy(): void;
}

export interface SileroVadOptions {
  onPhrase: (wavUri: string, phraseId: number) => void;
  onSpeechStart?: (phraseId: number) => void;
  onError?: (err: unknown) => void;
}

let _sessionPromise: Promise<InferenceSession> | null = null;

/** Eagerly initialize the silero session — call on app launch to absorb the
 *  ~200-500 ms model load before the first ambient toggle. Subsequent calls
 *  return the cached promise. */
export async function warmSilero(): Promise<InferenceSession> {
  if (!_sessionPromise) {
    _sessionPromise = (async () => {
      const asset = Asset.fromModule(require("../../../assets/silero_vad_v5.onnx"));
      await asset.downloadAsync();
      const uri = asset.localUri || asset.uri;
      if (!uri) throw new Error("[silero] asset has no uri");
      return await InferenceSession.create(uri);
    })().catch((err) => {
      _sessionPromise = null;
      throw err;
    });
  }
  return _sessionPromise;
}

export async function createSileroVad(opts: SileroVadOptions): Promise<SileroVadHandle> {
  const session = await warmSilero();
  const floatBuf = new Float32Array(FRAME_SAMPLES);
  let state = new Float32Array(STATE_LEN);
  const srData = BigInt64Array.from([BigInt(SAMPLE_RATE)]);

  let phraseId = 0;
  let inSpeech = false;
  let speechFrames: Float32Array[] = [];
  let preBuffer: Float32Array[] = [];
  let consecutivePositive = 0;
  let redemptionCount = 0;
  let destroyed = false;

  // Serialize inference. Each pushFrame appends to a chain so the state
  // tensor isn't mutated by two concurrent runs.
  let chain: Promise<void> = Promise.resolve();

  const resetPhrase = () => {
    speechFrames = [];
    consecutivePositive = 0;
    redemptionCount = 0;
  };

  const pushPre = (frame: Float32Array) => {
    preBuffer.push(frame);
    if (preBuffer.length > PRE_SPEECH_PAD_FRAMES) preBuffer.shift();
  };

  const writePhraseWav = (frames: Float32Array[]): string => {
    let total = 0;
    for (const f of frames) total += f.length;
    const byteLength = 44 + total * 2;
    const buf = new ArrayBuffer(byteLength);
    const view = new DataView(buf);
    const writeAscii = (off: number, s: string) => {
      for (let i = 0; i < s.length; i++) view.setUint8(off + i, s.charCodeAt(i));
    };
    writeAscii(0, "RIFF");
    view.setUint32(4, byteLength - 8, true);
    writeAscii(8, "WAVE");
    writeAscii(12, "fmt ");
    view.setUint32(16, 16, true);
    view.setUint16(20, 1, true);
    view.setUint16(22, 1, true);
    view.setUint32(24, SAMPLE_RATE, true);
    view.setUint32(28, SAMPLE_RATE * 2, true);
    view.setUint16(32, 2, true);
    view.setUint16(34, 16, true);
    writeAscii(36, "data");
    view.setUint32(40, total * 2, true);
    let off = 44;
    for (const f of frames) {
      for (let i = 0; i < f.length; i++) {
        const s = Math.max(-1, Math.min(1, f[i]));
        view.setInt16(off, s < 0 ? s * 0x8000 : s * 0x7fff, true);
        off += 2;
      }
    }
    const file = new File(Paths.cache, `vad_${Date.now()}_${phraseId}.wav`);
    file.create({ overwrite: true });
    file.write(new Uint8Array(buf));
    return file.uri;
  };

  const runInference = async (frameData: Float32Array): Promise<number> => {
    const input = new Tensor("float32", frameData, [1, FRAME_SAMPLES]);
    const stateTensor = new Tensor("float32", state, STATE_DIMS);
    const sr = new Tensor("int64", srData, [1]);
    const outputs = await session.run({ input, state: stateTensor, sr });
    const outKey = "output" in outputs ? "output" : Object.keys(outputs)[0];
    const stateKey = "stateN" in outputs ? "stateN" : (Object.keys(outputs).find((k) => k !== outKey) ?? Object.keys(outputs)[1]);
    const prob = (outputs[outKey].data as Float32Array)[0];
    state = new Float32Array(outputs[stateKey].data as Float32Array);
    return prob;
  };

  const processFrame = async (int16: Int16Array): Promise<void> => {
    if (destroyed || int16.length !== FRAME_SAMPLES) return;
    for (let i = 0; i < FRAME_SAMPLES; i++) floatBuf[i] = int16[i] / 32768;
    const frame = new Float32Array(floatBuf);

    let prob: number;
    try {
      prob = await runInference(floatBuf);
    } catch (err) {
      opts.onError?.(err);
      return;
    }
    if (destroyed) return;

    if (!inSpeech) {
      if (prob >= POS_THRESHOLD) {
        consecutivePositive++;
        if (consecutivePositive >= MIN_SPEECH_FRAMES) {
          inSpeech = true;
          phraseId++;
          speechFrames = [...preBuffer, frame];
          preBuffer = [];
          consecutivePositive = 0;
          redemptionCount = 0;
          opts.onSpeechStart?.(phraseId);
        } else {
          pushPre(frame);
        }
      } else {
        consecutivePositive = 0;
        pushPre(frame);
      }
      return;
    }

    speechFrames.push(frame);
    if (prob < NEG_THRESHOLD) {
      redemptionCount++;
      if (redemptionCount >= REDEMPTION_FRAMES) {
        const finalFrames = speechFrames;
        const id = phraseId;
        inSpeech = false;
        resetPhrase();
        if (finalFrames.length < MIN_SPEECH_FRAMES + REDEMPTION_FRAMES) {
          return; // misfire — too short
        }
        try {
          const uri = writePhraseWav(finalFrames);
          opts.onPhrase(uri, id);
        } catch (err) {
          opts.onError?.(err);
        }
      }
    } else {
      redemptionCount = 0;
    }
  };

  return {
    pushFrame(int16: Int16Array): void {
      if (destroyed) return;
      const owned = new Int16Array(int16);
      chain = chain.then(() => processFrame(owned)).catch((err) => {
        console.warn("[silero] frame error:", err);
      });
    },

    flush(): void {
      if (!inSpeech) return;
      const finalFrames = speechFrames;
      const id = phraseId;
      inSpeech = false;
      resetPhrase();
      if (finalFrames.length === 0) return;
      try {
        const uri = writePhraseWav(finalFrames);
        opts.onPhrase(uri, id);
      } catch (err) {
        opts.onError?.(err);
      }
    },

    destroy(): void {
      destroyed = true;
    },
  };
}
