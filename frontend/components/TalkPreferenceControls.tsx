"use client";

// Per-device talk-channel picker. Three rows (Desktop / Tablet / Phone),
// each a 3-way segmented control over Voice / Caption / Both. Stateless —
// the parent owns `value` and onChange, so this same control fits both
// the Onboarding form and the DebugPanel settings card.

import type { TalkChannel, TalkPreference } from "@/lib/api";

type DeviceRow = { key: keyof TalkPreference; label: string; emoji: string };

const ROWS: readonly DeviceRow[] = [
  { key: "desktop", label: "Desktop", emoji: "🖥" },
  { key: "tablet", label: "Tablet", emoji: "📱" },
  { key: "phone", label: "Phone", emoji: "📱" },
];

const CHANNELS: readonly { value: TalkChannel; label: string }[] = [
  { value: "voice", label: "Voice" },
  { value: "text", label: "Caption" },
  { value: "both", label: "Both" },
];

export default function TalkPreferenceControls({
  value,
  onChange,
  disabled,
  variant = "light",
}: {
  value: TalkPreference;
  onChange: (next: TalkPreference) => void;
  disabled?: boolean;
  variant?: "light" | "dim";
}) {
  const labelColor = variant === "dim"
    ? "text-gray-500 dark:text-gray-400"
    : "text-gray-600 dark:text-gray-300";
  return (
    <div className="space-y-2">
      {ROWS.map((row) => (
        <div
          key={row.key}
          className="flex items-center gap-3"
        >
          <div className={`w-20 text-sm ${labelColor}`}>
            <span className="mr-1">{row.emoji}</span>
            {row.label}
          </div>
          <div
            role="radiogroup"
            aria-label={`Talk channel on ${row.label}`}
            className="flex flex-1 rounded-lg border border-gray-300 dark:border-gray-700 overflow-hidden"
          >
            {CHANNELS.map((c) => {
              const active = value[row.key] === c.value;
              return (
                <button
                  key={c.value}
                  type="button"
                  role="radio"
                  aria-checked={active}
                  disabled={disabled}
                  onClick={() => onChange({ ...value, [row.key]: c.value })}
                  className={
                    "flex-1 px-2 py-1.5 text-xs font-medium transition-colors " +
                    (active
                      ? "bg-purple-600 text-white"
                      : "bg-white dark:bg-gray-900 text-gray-600 dark:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-800") +
                    " disabled:opacity-50"
                  }
                >
                  {c.label}
                </button>
              );
            })}
          </div>
        </div>
      ))}
    </div>
  );
}
