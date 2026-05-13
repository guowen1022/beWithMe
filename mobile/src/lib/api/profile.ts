import { api } from "./client";

export type TalkChannel = "voice" | "text" | "both";

export interface TalkPreference {
  desktop: TalkChannel;
  tablet: TalkChannel;
  phone: TalkChannel;
}

// Per the plan: phone defaults to "text" server-side. We override to "voice"
// during onboarding so the persona picks the Lane A voice-first prompt.
export const PHONE_VOICE_PREFERENCE: TalkPreference = {
  desktop: "both",
  tablet: "both",
  phone: "voice",
};

export async function updateTalkPreference(pref: TalkPreference): Promise<TalkPreference> {
  const res = await api("/api/talk-preference", {
    method: "PUT",
    body: JSON.stringify(pref),
  });
  if (!res.ok) throw new Error(`updateTalkPreference failed (${res.status})`);
  return res.json();
}

export async function getTalkPreference(): Promise<TalkPreference> {
  const res = await api("/api/talk-preference");
  if (!res.ok) throw new Error(`getTalkPreference failed (${res.status})`);
  return res.json();
}
