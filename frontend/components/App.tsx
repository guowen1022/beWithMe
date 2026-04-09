"use client";

import { useEffect, useState } from "react";
import { getProfile } from "@/lib/api";
import Onboarding from "./Onboarding";
import Reader from "./Reader";

export default function App() {
  const [profileLoaded, setProfileLoaded] = useState(false);
  const [hasProfile, setHasProfile] = useState(false);

  useEffect(() => {
    getProfile()
      .then((p) => {
        setHasProfile(!!p.self_description);
        setProfileLoaded(true);
      })
      .catch(() => {
        setProfileLoaded(true);
      });
  }, []);

  if (!profileLoaded) {
    return (
      <div className="flex flex-1 items-center justify-center h-screen">
        <p className="text-gray-400">Loading...</p>
      </div>
    );
  }

  if (!hasProfile) {
    return <Onboarding onComplete={() => setHasProfile(true)} />;
  }

  return <Reader />;
}
