// Voice machine state — mirrored on the state dot color.

export type VoiceMode =
  | "idle"
  | "ptt"
  | "ambient"
  | "transcribing"
  | "thinking"
  | "speaking";

export const MODE_COLOR: Record<VoiceMode, string> = {
  idle:         "#666666",
  ptt:          "#34d399",  // green, pulsing
  ambient:      "#22c55e",  // green, slow breath
  transcribing: "#eab308",
  thinking:     "#3b82f6",
  speaking:     "#d946ef",
};
