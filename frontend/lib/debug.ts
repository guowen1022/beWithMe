// Single master switch for developer debug surfaces: the Mirror nav/page and
// the top-right teacher-thinking panel. (The desktop shell gates Chromium
// DevTools on the same BEWITHME_DEBUG value in desktop/src/main.ts.)
//
// Default ON — only an explicit "0" disables. Set via NEXT_PUBLIC_BEWITHME_DEBUG,
// which scripts/dev-desktop.sh fans out from BEWITHME_DEBUG. Must stay a direct,
// static process.env reference so Next.js inlines it into the client bundle.
export const DEBUG_UI = process.env.NEXT_PUBLIC_BEWITHME_DEBUG !== "0";
