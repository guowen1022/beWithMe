"use client";

import { useEffect, useState } from "react";
import {
  getProfile,
  getCurrentUserId,
  clearCurrentUserId,
  listUsers,
  UnknownUserError,
  type User,
} from "@/lib/api";
import UserSelector from "./UserSelector";
import Onboarding from "./Onboarding";
import Reader from "./Reader";

export default function App() {
  const [userId, setUserId] = useState<string | null>(null);
  const [profileLoaded, setProfileLoaded] = useState(false);
  const [hasProfile, setHasProfile] = useState(false);
  const [username, setUsername] = useState<string | null>(null);

  function handleSwitchUser() {
    clearCurrentUserId();
    setUserId(null);
    setHasProfile(false);
    setProfileLoaded(false);
    setUsername(null);
  }

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
          handleSwitchUser();
          return;
        }
        setProfileLoaded(true);
      });
  }, [userId]);

  // Resolve username for the header badge
  useEffect(() => {
    if (!userId) return;
    listUsers()
      .then((users: User[]) => {
        const match = users.find((u) => u.id === userId);
        setUsername(match?.username ?? null);
      })
      .catch(() => setUsername(null));
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
    return (
      <>
        <Onboarding onComplete={() => setHasProfile(true)} />
        <UserBadge username={username} onSwitch={handleSwitchUser} />
      </>
    );
  }

  // Step 4: Main reader
  return (
    <>
      <Reader />
      <UserBadge username={username} onSwitch={handleSwitchUser} />
    </>
  );
}

function UserBadge({
  username,
  onSwitch,
}: {
  username: string | null;
  onSwitch: () => void;
}) {
  return (
    <div className="fixed top-2 right-2 z-50 flex items-center gap-2 rounded-full bg-white/90 px-3 py-1.5 text-xs shadow-md backdrop-blur border border-gray-200">
      <span className="text-gray-600">
        {username ? <>Signed in as <b className="text-gray-800">{username}</b></> : "Signed in"}
      </span>
      <button
        onClick={onSwitch}
        className="text-blue-600 hover:text-blue-800 font-medium"
      >
        Switch
      </button>
    </div>
  );
}
