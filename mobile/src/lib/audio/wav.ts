// PCM16 mono → WAV bytes. Port of frontend/lib/vad.ts:encodeWavPcm16.

export function encodeWavPcm16(samples: Int16Array, sampleRate: number = 16000): Uint8Array {
  const numChannels = 1;
  const bytesPerSample = 2;
  const blockAlign = numChannels * bytesPerSample;
  const byteRate = sampleRate * blockAlign;
  const dataSize = samples.byteLength;
  const fileSize = 44 + dataSize;

  const buf = new ArrayBuffer(fileSize);
  const view = new DataView(buf);
  let p = 0;
  const writeStr = (s: string) => { for (let i = 0; i < s.length; i++) view.setUint8(p++, s.charCodeAt(i)); };
  const writeU32 = (n: number) => { view.setUint32(p, n, true); p += 4; };
  const writeU16 = (n: number) => { view.setUint16(p, n, true); p += 2; };

  writeStr("RIFF");
  writeU32(fileSize - 8);
  writeStr("WAVE");
  writeStr("fmt ");
  writeU32(16);            // subchunk1 size
  writeU16(1);             // audio format PCM
  writeU16(numChannels);
  writeU32(sampleRate);
  writeU32(byteRate);
  writeU16(blockAlign);
  writeU16(16);            // bits per sample
  writeStr("data");
  writeU32(dataSize);

  // Sample payload as little-endian 16-bit signed
  const samplesBytes = new Uint8Array(buf, 44, dataSize);
  const srcBytes = new Uint8Array(samples.buffer, samples.byteOffset, samples.byteLength);
  samplesBytes.set(srcBytes);

  return new Uint8Array(buf);
}
