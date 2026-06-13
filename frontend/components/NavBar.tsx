"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import {
  clearCurrentUserId,
  getCurrentUserId,
  listUsers,
  type User,
} from "@/lib/api";
import { DEBUG_UI } from "@/lib/debug";

// The launcher feed is the landing surface (App owns launcher↔reader view
// state at "/"), so the nav is intentionally light: the brand returns home
// (to the feed) and Mirror is the event-stream debug view. Mirror is a debug
// surface, so it's hidden when DEBUG_UI is off (BEWITHME_DEBUG=0).
const NAV_ITEMS = DEBUG_UI ? [{ href: "/mirror", label: "Mirror" }] : [];

export default function NavBar() {
  const pathname = usePathname();
  const router = useRouter();
  const [username, setUsername] = useState<string | null>(null);

  useEffect(() => {
    function resolve() {
      const id = getCurrentUserId();
      if (!id) {
        setUsername(null);
        return;
      }
      listUsers()
        .then((users: User[]) => {
          const match = users.find((u) => u.id === id);
          setUsername(match?.username ?? null);
        })
        .catch(() => setUsername(null));
    }
    resolve();
    window.addEventListener("bewithme:user-changed", resolve);
    return () => window.removeEventListener("bewithme:user-changed", resolve);
  }, [pathname]);

  function handleSwitch() {
    clearCurrentUserId();
    window.dispatchEvent(new CustomEvent("bewithme:user-changed"));
  }

  function goHome() {
    // From a sub-route (/mirror) this navigates back to the app; on "/" it
    // tells App to switch from the Reader back to the launcher feed.
    window.dispatchEvent(new CustomEvent("bewithme:go-home"));
    if (pathname !== "/") router.push("/");
  }

  return (
    <nav className="bg-[var(--bw-void-2)] border-b border-[var(--bw-border)] px-4">
      <div className="max-w-6xl mx-auto flex items-center h-12 gap-6">
        <button
          onClick={goHome}
          className="font-bold text-[var(--bw-ink)] mr-4 tracking-tight hover:opacity-80 transition-opacity"
        >
          beWithMe
        </button>
        {NAV_ITEMS.map((item) => {
          const active = pathname.startsWith(item.href);
          return (
            <Link
              key={item.href}
              href={item.href}
              className={`text-sm font-medium pb-0.5 border-b-2 transition-colors ${
                active
                  ? "border-[var(--bw-accent)] text-[var(--bw-accent)]"
                  : "border-transparent text-[var(--bw-ink-muted)] hover:text-[var(--bw-ink)]"
              }`}
            >
              {item.label}
            </Link>
          );
        })}

        {username && (
          <div className="ml-auto flex items-center gap-2 text-xs">
            <span className="text-[var(--bw-ink-muted)]">
              Signed in as <b className="text-[var(--bw-ink)]">{username}</b>
            </span>
            <button
              onClick={handleSwitch}
              className="text-[var(--bw-accent)] hover:opacity-80 font-medium transition-opacity"
            >
              Switch
            </button>
          </div>
        )}
      </div>
    </nav>
  );
}
