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
import CanvasCommandBar from "./CanvasCommandBar";

export default function App() {
  const [userId, setUserId] = useState<string | null>(null);
  const [profileLoaded, setProfileLoaded] = useState(false);
  const [hasProfile, setHasProfile] = useState(false);
  const [goalMode, setGoalMode] = useState(false);

  // NavBar owns the user-switch UI and dispatches this event when the user
  // clicks "Switch". Reset our local state so we fall back to UserSelector.
  useEffect(() => {
    function onUserChanged() {
      setUserId(null);
      setHasProfile(false);
      setProfileLoaded(false);
      setGoalMode(false);
    }
    window.addEventListener("bewithme:user-changed", onUserChanged);
    return () =>
      window.removeEventListener("bewithme:user-changed", onUserChanged);
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

  // Step 4: Goal planner or canvas (the new reader surface).
  if (goalMode) {
    return <GoalPlanner onBack={() => setGoalMode(false)} />;
  }

  // Canvas IS the reader. Inline render at "/", below the root NavBar.
  // No /canvas route, no chromeless wrapper, no redirects.
  return (
    <div className="relative flex-1 bg-[#0a0a0a]">
      <DynamicSurface mode="fullscreen" />
      <CanvasCommandBar />
    </div>
  );
}
