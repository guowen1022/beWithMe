"use client";

import { useState } from "react";
import {
  updateProfile,
  updateTalkPreference,
  DEFAULT_TALK_PREFERENCE,
  type TalkPreference,
} from "@/lib/api";
import TalkPreferenceControls from "./TalkPreferenceControls";

export default function Onboarding({
  onComplete,
}: {
  onComplete: (desc: string) => void;
}) {
  const [description, setDescription] = useState("");
  const [talkPref, setTalkPref] = useState<TalkPreference>(DEFAULT_TALK_PREFERENCE);
  const [saving, setSaving] = useState(false);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!description.trim()) return;
    setSaving(true);
    try {
      await updateProfile(description);
      await updateTalkPreference(talkPref);
      onComplete(description);
    } catch (err) {
      console.error(err);
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="flex flex-1 items-center justify-center p-8">
      <form onSubmit={handleSubmit} className="w-full max-w-lg space-y-6">
        <div>
          <h1 className="text-2xl font-semibold">Welcome to beWithMe</h1>
          <p className="mt-2 text-gray-600">
            Tell me about yourself — your background, what you&apos;re studying,
            how you like to learn. This helps me personalize my answers.
          </p>
        </div>
        <textarea
          value={description}
          onChange={(e) => setDescription(e.target.value)}
          rows={6}
          className="w-full rounded-lg border border-gray-300 p-3 text-sm focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
          placeholder="e.g. I'm a grad student in computational neuroscience. I'm comfortable with linear algebra and probability but new to deep learning. I prefer intuitive explanations before formalism..."
        />

        <div>
          <h2 className="text-sm font-medium text-gray-700">
            How should I talk to you?
          </h2>
          <p className="mt-1 text-xs text-gray-500">
            Pick a channel per device. <strong>Voice</strong> plays Kokoro
            audio. <strong>Caption</strong> shows a YouTube-style line at the
            bottom. <strong>Both</strong> does both.
          </p>
        </div>
        <TalkPreferenceControls value={talkPref} onChange={setTalkPref} />

        <button
          type="submit"
          disabled={saving || !description.trim()}
          className="rounded-lg bg-blue-600 px-6 py-2.5 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-50"
        >
          {saving ? "Saving..." : "Get Started"}
        </button>
      </form>
    </div>
  );
}
