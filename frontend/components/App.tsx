"use client";

import { useEffect, useState } from "react";
import { getProfile, getCurrentUserId } from "@/lib/api";
import UserSelector from "./UserSelector";
import Onboarding from "./Onboarding";
import Reader from "./Reader";

export default function App() {
  const [userId, setUserId] = useState<string | null>(null);
  const [profileLoaded, setProfileLoaded] = useState(false);
  const [hasProfile, setHasProfile] = useState(false);

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
      .catch(() => {
        setProfileLoaded(true);
      });
  }, [userId]);

  // Step 1: User selection
  if (!userId) {
    return <UserSelector onUserSelected={setUserId} />;
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

  // Step 4: Main reader
  return <Reader />;
}
