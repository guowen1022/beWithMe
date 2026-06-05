"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";
import {
  clearCurrentUserId,
  getCurrentUserId,
  listUsers,
  type User,
} from "@/lib/api";

const NAV_ITEMS = [
  { href: "/", label: "Reader" },
  { href: "/recommendations", label: "Recommendations" },
  { href: "/inbox", label: "Inbox" },
  { href: "/mirror", label: "Mirror" },
];

export default function NavBar() {
  const pathname = usePathname();
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

  return (
    <nav className="bg-white border-b border-gray-200 px-4">
      <div className="max-w-6xl mx-auto flex items-center h-12 gap-6">
        <span className="font-semibold text-gray-900 mr-4">beWithMe</span>
        {NAV_ITEMS.map((item) => {
          const active = item.href === "/" ? pathname === "/" : pathname.startsWith(item.href);
          return (
            <Link
              key={item.href}
              href={item.href}
              className={`text-sm font-medium pb-0.5 border-b-2 transition-colors ${
                active
                  ? "border-blue-600 text-blue-600"
                  : "border-transparent text-gray-500 hover:text-gray-700"
              }`}
            >
              {item.label}
            </Link>
          );
        })}

        {username && (
          <div className="ml-auto flex items-center gap-2 text-xs">
            <span className="text-gray-500">
              Signed in as <b className="text-gray-800">{username}</b>
            </span>
            <button
              onClick={handleSwitch}
              className="text-blue-600 hover:text-blue-800 font-medium"
            >
              Switch
            </button>
          </div>
        )}
      </div>
    </nav>
  );
}
