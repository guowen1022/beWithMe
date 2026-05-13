import { create } from "zustand";
import type { VoiceMode } from "./mode";

interface AppState {
  voiceMode: VoiceMode;
  setVoiceMode: (m: VoiceMode) => void;
}

export const useAppStore = create<AppState>((set) => ({
  voiceMode: "idle",
  setVoiceMode: (m) => set({ voiceMode: m }),
}));
