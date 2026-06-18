import { create } from "zustand";
import type { VoiceMode } from "./mode";
import { uuidv4 } from "../config";

interface AppState {
  voiceMode: VoiceMode;
  setVoiceMode: (m: VoiceMode) => void;
  // The current learning-session id. One per app launch, sent on every ask
  // so the backend tags interactions and can record the session on end.
  // Reset to a fresh id on go_home (end_session). Mirrors the web's
  // per-page-mount session id in CanvasCommandBar.
  sessionId: string;
  newSession: () => void;
}

export const useAppStore = create<AppState>((set) => ({
  voiceMode: "idle",
  setVoiceMode: (m) => set({ voiceMode: m }),
  sessionId: uuidv4(),
  newSession: () => set({ sessionId: uuidv4() }),
}));
