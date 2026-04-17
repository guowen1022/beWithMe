"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

const NAV_ITEMS = [
  { href: "/", label: "Reader" },
  { href: "/recommendations", label: "Recommendations" },
];

export default function NavBar() {
  const pathname = usePathname();

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
      </div>
    </nav>
  );
}
