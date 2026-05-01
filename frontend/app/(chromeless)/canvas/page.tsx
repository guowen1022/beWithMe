"use client";

import { useSyncExternalStore } from "react";
import Link from "next/link";
import DynamicSurface from "@/components/DynamicSurface";
import CanvasCommandBar from "@/components/CanvasCommandBar";

const USER_KEY = "bewithme_user_id";

function subscribeToStorage(cb: () => void): () => void {
  window.addEventListener("storage", cb);
  return () => window.removeEventListener("storage", cb);
}
function readUserId(): string | null {
  return localStorage.getItem(USER_KEY);
}

export default function CanvasPage() {
  // useSyncExternalStore avoids the setState-in-effect anti-pattern;
  // the third argument keeps SSR happy by yielding a stable null
  // before hydration.
  const userId = useSyncExternalStore(subscribeToStorage, readUserId, () => null);

  if (!userId) {
    return (
      <div className="flex items-center justify-center h-screen text-gray-200 text-sm">
        Pick a user at <Link className="ml-1 underline" href="/">/</Link> first — the canvas needs an active user to subscribe to.
      </div>
    );
  }

  return (
    <>
      <DynamicSurface mode="fullscreen" />
      <CanvasCommandBar />
    </>
  );
}
