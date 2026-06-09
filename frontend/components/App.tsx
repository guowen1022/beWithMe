"use client";

import { useEffect, useState } from "react";
import {
  getProfile,
  getCurrentUserId,
  UnknownUserError,
} from "@/lib/api";
import UserSelector from "./UserSelector";
import Onboarding from "./Onboarding";
import GoalPlanner from "./GoalPlanner";
import DynamicSurface from "./DynamicSurface";
import SessionLauncher from "./SessionLauncher";

type View = "launcher" | "reader";

export default function App() {
  const [userId, setUserId] = useState<string | null>(null);
  const [profileLoaded, setProfileLoaded] = useState(false);
  const [hasProfile, setHasProfile] = useState(false);
  const [goalMode, setGoalMode] = useState(false);
  // The launcher feed is the landing surface; the Reader is entered by
  // picking a card (or "start from scratch").
  const [view, setView] = useState<View>("launcher");
  // True when the Reader was entered by picking a feed card ("Begin"): the
  // thread auto-starts from the seeded turn, so we suppress the `lets_begin`
  // welcome card. "Start from scratch" / go-home leave this false.
  const [autostart, setAutostart] = useState(false);

  // NavBar owns the user-switch UI and dispatches this event when the user
  // clicks "Switch". Reset our local state so we fall back to UserSelector.
  useEffect(() => {
    function onUserChanged() {
      setUserId(null);
      setHasProfile(false);
      setProfileLoaded(false);
      setGoalMode(false);
      setView("launcher");
      setAutostart(false);
    }
    window.addEventListener("bewithme:user-changed", onUserChanged);
    return () =>
      window.removeEventListener("bewithme:user-changed", onUserChanged);
  }, []);

  // NavBar's brand click dispatches this to return to the launcher feed.
  useEffect(() => {
    function onGoHome() {
      setGoalMode(false);
      setView("launcher");
      setAutostart(false);
    }
    window.addEventListener("bewithme:go-home", onGoHome);
    return () => window.removeEventListener("bewithme:go-home", onGoHome);
  }, []);

  // Check for persisted user on mount
  useEffect(() => {
    const stored = getCurrentUserId();
    if (stored) {
      setUserId(stored);
    }
  }, []);

  // Load profile once user is selected
  useEffect(() => {
    if (!userId) return;
    setProfileLoaded(false);
    getProfile()
      .then((p) => {
        setHasProfile(!!p.self_description);
        setProfileLoaded(true);
      })
      .catch((err) => {
        if (err instanceof UnknownUserError) {
          setUserId(null);
          setHasProfile(false);
          setProfileLoaded(false);
          window.dispatchEvent(new CustomEvent("bewithme:user-changed"));
          return;
        }
        setProfileLoaded(true);
      });
  }, [userId]);

  function handleUserSelected(id: string) {
    // Don't dispatch `bewithme:user-changed` here — that event means
    // "switch / sign out", and our own listener resets userId back to
    // null on it. NavBar dispatches it when the user clicks "Switch".
    setUserId(id);
  }

  // Step 1: User selection
  if (!userId) {
    return <UserSelector onUserSelected={handleUserSelected} />;
  }

  // Step 2: Loading profile
  if (!profileLoaded) {
    return (
      <div className="flex flex-1 items-center justify-center h-screen">
        <p className="text-gray-400">Loading...</p>
      </div>
    );
  }

  // Step 3: Onboarding if no profile
  if (!hasProfile) {
    return <Onboarding onComplete={() => setHasProfile(true)} />;
  }

  // Step 4: Goal planner, launcher feed, or canvas (the reader surface).
  if (goalMode) {
    return <GoalPlanner onBack={() => setGoalMode(false)} />;
  }

  // The launcher feed is the landing surface. Picking a card (or "start from
  // scratch") seeds the command bar and switches into the Reader.
  if (view === "launcher") {
    return (
      <SessionLauncher
        onEnterReader={(autostarting = false) => {
          setAutostart(autostarting);
          setView("reader");
        }}
      />
    );
  }

  // Canvas IS the reader. Full-bleed, no chrome above it. The command bar and
  // teacher-thinking panel render as `system:` blocks inside DynamicSurface so
  // they share the same grid + drag system as every other block on the canvas.
  return (
    <div className="relative flex-1 bg-[var(--bw-void)]">
      <DynamicSurface mode="fullscreen" suppressWelcome={autostart} />
    </div>
  );
}
