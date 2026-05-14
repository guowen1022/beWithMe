// The voice control block — Phase 1's only canvas block. RN port of
// frontend/templates/blocks/ambient_mic.js. Renders a centered state dot;
// holds the voice machine state. Gestures:
//   - press-in/press-out: PTT (record while held)
//   - double-tap: toggle ambient on/off
//   - long-press (1.5 s): open Settings screen
//
// Phase 1 step 4 (this file): just the dot + gesture wiring. Real audio
// lives behind the recorder/player/vad stubs and lights up in later steps.

import React, { useCallback, useEffect, useRef, useState } from "react";
import { Alert, Animated, Easing, Pressable, StyleSheet, Text, View } from "react-native";
import { useNavigation } from "@react-navigation/native";
import type { NativeStackNavigationProp } from "@react-navigation/native-stack";
import { useAppStore } from "../../state/store";
import { MODE_COLOR, type VoiceMode } from "../../state/mode";
import { micArbiter } from "../../lib/micArbiter";
import { buildHelpers } from "../helpers";
import { ambientMicManifest } from "./AmbientMicBlock.manifest";
import { startRecording, type RecorderHandleV2 } from "../../lib/audio/recorder";
import { ensureMicPermission } from "../../lib/audio/permissions";
import { runVoiceTurn, runVoiceTurnFromText, transcribeOnly } from "../../lib/session/voiceTurn";
import { createSileroVad, type SileroVadHandle } from "../../lib/vad/sileroVad";
import { createEouGate, type EouGate } from "../../lib/eouGate";
import { stopPlayer } from "../../lib/audio/player";
import type { BlockProps } from "../blockRegistry";
import type { RootStackParamList } from "../../navigation/RootNavigator";

const DOT_SIZE = 120;
const DOUBLE_TAP_MS = 350;

export function AmbientMicBlock({ blockId }: BlockProps): React.ReactElement {
  const navigation = useNavigation<NativeStackNavigationProp<RootStackParamList>>();
  const voiceMode = useAppStore((s) => s.voiceMode);
  const setVoiceMode = useAppStore((s) => s.setVoiceMode);
  const [ambientOn, setAmbientOn] = useState(false);

  const helpersRef = useRef(buildHelpers(blockId, ambientMicManifest));
  const lastTapAt = useRef(0);

  // PTT capture state. The native recorder accumulates samples and writes
  // a WAV on stop(); we just dispatch the resulting URI to runVoiceTurn.
  const recorderRef = useRef<RecorderHandleV2 | null>(null);
  const turnAbortRef = useRef<AbortController | null>(null);
  // Minimum hold for PTT to count as intentional. Sub-threshold taps just
  // close the recorder and skip the turn entirely so quick clicks don't
  // queue up empty/hallucinated turns.
  const pttStartRef = useRef<number>(0);
  const PTT_MIN_HOLD_MS = 300;

  // Ambient capture state: a long-running AudioRecord whose Int16 frames are
  // fed to silero-vad in JS. On each detected phrase, the VAD writes a WAV
  // and we dispatch it like a PTT turn. The recorder + VAD are torn down
  // while a turn is in flight so the persona's TTS doesn't echo-loop into
  // the next ambient phrase.
  const ambientRecorderRef = useRef<RecorderHandleV2 | null>(null);
  const ambientVadRef = useRef<SileroVadHandle | null>(null);
  const ambientUnsubFrameRef = useRef<(() => void) | null>(null);
  const ambientTurnInFlight = useRef(false);
  const ambientOnRef = useRef(false);
  // EOU gate: silero fires onPhrase every ~640ms of silence, but real
  // utterances bleed through disfluencies ("what is the... uh..."). The
  // gate transcribes each phrase, buffers it, and only fires onCommit
  // when /api/eou judges the merged transcript a real end-of-turn (or
  // the hard timeout / max-phrases fallback). Falls open if EOU is
  // unconfigured → behavior matches today.
  const ambientGateRef = useRef<EouGate | null>(null);

  // Pulse / breathe animation depending on state.
  const pulse = useRef(new Animated.Value(0)).current;
  useEffect(() => {
    pulse.stopAnimation();
    if (voiceMode === "ptt" || voiceMode === "ambient") {
      const duration = voiceMode === "ptt" ? 600 : 2400;
      const loop = Animated.loop(
        Animated.sequence([
          Animated.timing(pulse, { toValue: 1, duration: duration / 2, easing: Easing.inOut(Easing.ease), useNativeDriver: true }),
          Animated.timing(pulse, { toValue: 0, duration: duration / 2, easing: Easing.inOut(Easing.ease), useNativeDriver: true }),
        ]),
      );
      loop.start();
      return () => loop.stop();
    }
    pulse.setValue(0);
    return undefined;
  }, [voiceMode, pulse]);

  // Publish initial bus state (matches web: ambient_mic.muted).
  useEffect(() => {
    helpersRef.current.bus.publish("ambient_mic.muted", !ambientOn);
  }, [ambientOn]);

  const stopAmbient = useCallback(async () => {
    ambientUnsubFrameRef.current?.();
    ambientUnsubFrameRef.current = null;
    ambientVadRef.current?.destroy();
    ambientVadRef.current = null;
    // Drop any buffered EOU phrases — committing them after teardown
    // would surface a turn the user thought they cancelled.
    ambientGateRef.current?.reset();
    ambientGateRef.current = null;
    const rec = ambientRecorderRef.current;
    ambientRecorderRef.current = null;
    if (rec) { try { await rec.stop(); } catch { /* ignore */ } }
  }, []);

  const startAmbient = useCallback(async () => {
    if (ambientRecorderRef.current || ambientVadRef.current) return;
    const ok = await ensureMicPermission();
    if (!ok) {
      Alert.alert("Mic unavailable", "Grant microphone permission to use ambient mode.");
      setAmbientOn(false);
      micArbiter.release("ambient");
      setVoiceMode("idle");
      return;
    }
    try {
      // Build the gate first so onPhrase below can ingest into it.
      // onCommit fires once the merged user turn is judged complete —
      // that's the moment we engage the persona.
      const gate = createEouGate({
        onCommit: ({ text, totalDurationS }) => {
          if (ambientTurnInFlight.current) return;
          ambientTurnInFlight.current = true;
          turnAbortRef.current?.abort();
          const ctl = new AbortController();
          turnAbortRef.current = ctl;
          runVoiceTurnFromText(text, totalDurationS, {
            setMode: setVoiceMode,
            onError: (err) => console.warn("[ambient_mic] turn error:", err),
          }, ctl.signal).finally(() => {
            ambientTurnInFlight.current = false;
            if (turnAbortRef.current === ctl) turnAbortRef.current = null;
            if (ambientOnRef.current) setVoiceMode("ambient");
          });
        },
        onDecision: (info) => {
          console.log("[ambient_mic] eou commit:", info);
        },
      });
      ambientGateRef.current = gate;

      const vad = await createSileroVad({
        // Barge-in: when speech begins while a turn is in flight (whether
        // we're transcribing/thinking/speaking), abort the in-flight turn
        // and stop the AudioTrack. The subsequent onPhrase will fire the
        // new turn naturally. AEC + server-side echo dedup keep this from
        // self-triggering off our own TTS playback.
        //
        // Gate state during barge-in: the gate is already empty (cleared
        // on the prior commit), so no reset is needed. New phrases will
        // ingest into a fresh empty gate and start a new turn buffer.
        onSpeechStart: () => {
          if (!ambientTurnInFlight.current) return;
          turnAbortRef.current?.abort();
          turnAbortRef.current = null;
          stopPlayer().catch(() => {});
          ambientTurnInFlight.current = false;
        },
        onPhrase: (wavUri, phraseId) => {
          // While a committed turn is running, don't accept new phrases —
          // barge-in (onSpeechStart) is the path for interrupting. silero
          // can still fire onPhrase mid-turn if a short utterance fully
          // ended before the player even started; drop it.
          if (ambientTurnInFlight.current) return;
          setVoiceMode("transcribing");
          transcribeOnly(wavUri).then((trans) => {
            if (!trans) return;
            if (trans.isNoise) {
              // Whisper hallucination on click/silence — don't ingest.
              if (ambientOnRef.current && !ambientTurnInFlight.current) {
                setVoiceMode("ambient");
              }
              return;
            }
            // Back to listening while the gate decides. The dot stays
            // green-ambient until the gate commits and runVoiceTurnFromText
            // flips it to "thinking".
            if (ambientOnRef.current && !ambientTurnInFlight.current) {
              setVoiceMode("ambient");
            }
            ambientGateRef.current?.ingest(trans.text, phraseId, trans.durationS);
          }).catch((err) => {
            console.warn("[ambient_mic] transcribe failed:", err);
            if (ambientOnRef.current && !ambientTurnInFlight.current) {
              setVoiceMode("ambient");
            }
          });
        },
        onError: (err) => console.warn("[ambient_mic] vad error:", err),
      });
      const rec = await startRecording({ sampleRate: 16000, frameSamples: 512, accumulateWav: false });
      ambientVadRef.current = vad;
      ambientRecorderRef.current = rec;
      ambientUnsubFrameRef.current = rec.onFrame((frame) => vad.pushFrame(frame));
      setVoiceMode("ambient");
    } catch (e) {
      console.warn("[ambient_mic] startAmbient failed:", e);
      Alert.alert("Ambient mic unavailable", String(e));
      setAmbientOn(false);
      micArbiter.release("ambient");
      setVoiceMode("idle");
    }
  }, [setVoiceMode, stopAmbient]);

  // Keep a ref in sync so the async callbacks in startAmbient can see the
  // latest toggle state without stale-closure tearing.
  useEffect(() => { ambientOnRef.current = ambientOn; }, [ambientOn]);

  // Tear down on unmount.
  useEffect(() => () => { void stopAmbient(); }, [stopAmbient]);

  const onPressIn = useCallback(async () => {
    if (!micArbiter.acquire("ptt")) return;
    pttStartRef.current = Date.now();
    setVoiceMode("ptt");

    // Barge-in via PTT: abort any in-flight ambient turn + stop TTS so the
    // user's PTT utterance lands cleanly without lingering speaker audio.
    turnAbortRef.current?.abort();
    turnAbortRef.current = null;
    stopPlayer().catch(() => {});
    ambientTurnInFlight.current = false;

    // PTT preempts ambient: shut down the long-running AudioRecord + VAD so
    // a fresh PTT session can take the mic. Only one AudioRecord can hold
    // VOICE_COMMUNICATION at a time.
    if (ambientRecorderRef.current || ambientVadRef.current) {
      await stopAmbient();
    }

    const ok = await ensureMicPermission();
    if (!ok) {
      micArbiter.release("ptt");
      setVoiceMode(ambientOn ? "ambient" : "idle");
      if (ambientOn) void startAmbient();
      return;
    }

    try {
      const handle = await startRecording({ sampleRate: 16000, frameSamples: 512 });
      recorderRef.current = handle;
    } catch (e) {
      console.warn("[ambient_mic] startRecording failed:", e);
      micArbiter.release("ptt");
      setVoiceMode(ambientOn ? "ambient" : "idle");
      if (ambientOn) void startAmbient();
      Alert.alert("Mic unavailable", String(e));
    }
  }, [setVoiceMode, ambientOn, stopAmbient, startAmbient]);

  const onPressOut = useCallback(async () => {
    const recorder = recorderRef.current;
    recorderRef.current = null;
    micArbiter.release("ptt");
    const heldMs = Date.now() - pttStartRef.current;

    if (!recorder) {
      setVoiceMode(ambientOn ? "ambient" : "idle");
      if (ambientOn) void startAmbient();
      return;
    }

    const wavUri = await recorder.stop();
    // Drop short presses entirely — they're click-noise, not utterances.
    if (!wavUri || heldMs < PTT_MIN_HOLD_MS) {
      setVoiceMode(ambientOn ? "ambient" : "idle");
      if (ambientOn) void startAmbient();
      return;
    }

    turnAbortRef.current?.abort();
    const ctl = new AbortController();
    turnAbortRef.current = ctl;
    runVoiceTurn(wavUri, {
      setMode: setVoiceMode,
      onError: (err) => console.warn("[ambient_mic] turn error:", err),
    }, ctl.signal).finally(() => {
      if (turnAbortRef.current === ctl) turnAbortRef.current = null;
      // After the turn, return to ambient if still toggled, else idle.
      if (ambientOnRef.current) void startAmbient();
      else setVoiceMode("idle");
    });
  }, [setVoiceMode, ambientOn, startAmbient]);

  const onPress = useCallback(() => {
    const now = Date.now();
    if (now - lastTapAt.current < DOUBLE_TAP_MS) {
      // Double tap: toggle ambient mode.
      lastTapAt.current = 0;
      const next = !ambientOn;
      setAmbientOn(next);
      if (next) {
        if (micArbiter.acquire("ambient")) { void startAmbient(); }
      } else {
        micArbiter.release("ambient");
        void stopAmbient();
        setVoiceMode("idle");
      }
      return;
    }
    lastTapAt.current = now;
  }, [ambientOn, setVoiceMode, startAmbient, stopAmbient]);

  const dotColor = MODE_COLOR[voiceMode];
  const scale = pulse.interpolate({ inputRange: [0, 1], outputRange: [1, 1.12] });
  const haloOpacity = pulse.interpolate({ inputRange: [0, 1], outputRange: [0.15, 0.45] });

  return (
    <View style={styles.container}>
      <Pressable
        onPressIn={onPressIn}
        onPressOut={onPressOut}
        onPress={onPress}
        hitSlop={20}
        style={styles.target}
      >
        <Animated.View
          style={[
            styles.halo,
            { backgroundColor: dotColor, opacity: haloOpacity, transform: [{ scale }] },
          ]}
          pointerEvents="none"
        />
        <Animated.View
          style={[styles.dot, { backgroundColor: dotColor, transform: [{ scale }] }]}
          pointerEvents="none"
        />
      </Pressable>
      <Pressable
        onPress={() => navigation.navigate("Settings")}
        hitSlop={16}
        style={styles.settingsButton}
      >
        <Text style={styles.settingsGlyph}>⚙</Text>
      </Pressable>
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    alignItems: "center",
    justifyContent: "center",
  },
  target: {
    width: DOT_SIZE * 1.8,
    height: DOT_SIZE * 1.8,
    alignItems: "center",
    justifyContent: "center",
  },
  halo: {
    position: "absolute",
    width: DOT_SIZE * 1.6,
    height: DOT_SIZE * 1.6,
    borderRadius: (DOT_SIZE * 1.6) / 2,
  },
  dot: {
    width: DOT_SIZE,
    height: DOT_SIZE,
    borderRadius: DOT_SIZE / 2,
  },
  settingsButton: {
    position: "absolute",
    top: 12,
    right: 12,
    width: 44,
    height: 44,
    alignItems: "center",
    justifyContent: "center",
  },
  settingsGlyph: {
    color: "#6b6f7d",
    fontSize: 22,
    lineHeight: 24,
  },
});

// Suppress unused-var lint on VoiceMode (referenced via MODE_COLOR keying).
export type _Phase1VoiceMode = VoiceMode;
