// StyleSheet realization of the note class vocabulary.
//
// Mirrors the .bw-card ruleset in frontend/app/globals.css and the
// ALLOWED_CLASSES set in infra/render/note_grammar.py — the
// parity test (tests/unit/test_class_allowlist_parity.py) diffs the
// keys here against the Python source of truth.
//
// CSS variable values are hardcoded here since RN StyleSheet has no
// var() support. Keep colors in sync with the --bw-* tokens in
// globals.css.

import { StyleSheet } from "react-native";

// Color tokens copied from frontend/app/globals.css :root.
const BW = {
  surface:        "#12121E",
  surface2:       "#1A1A2A",
  border:         "#262638",
  borderStrong:   "#3A3A52",
  ink:            "#F4F4F8",
  inkMuted:       "#9090A8",
  inkFaint:       "#4F4F66",
  accent:         "#5C8CE6",
  accentSoft:     "rgba(92, 140, 230, 0.16)",
  danger:         "#E06464",
  warn:           "#D6A558",
  success:        "#62B285",
  info:           "#5C8CE6",
  revisionAddBg:    "rgba(98, 178, 133, 0.18)",
  revisionAddFg:    "#9BD9B1",
  revisionRemBg:    "rgba(224, 100, 100, 0.16)",
  revisionRemFg:    "#EFA2A2",
};

// Tagless prose primitives — referenced by the mapper, not by class
// name. Persona doesn't author these; the mapper applies them based on
// HTML tag (h1..h4, p, a, code, etc).
export const TAG_STYLES = StyleSheet.create({
  root:        { flex: 1, paddingHorizontal: 22, paddingVertical: 18 },
  block:       { marginVertical: 6 },
  h1:          { fontSize: 24, fontWeight: "800", color: BW.ink, marginTop: 18, marginBottom: 6 },
  h2:          { fontSize: 20, fontWeight: "700", color: BW.ink, marginTop: 16, marginBottom: 6 },
  h3:          { fontSize: 17, fontWeight: "700", color: BW.ink, marginTop: 14, marginBottom: 6 },
  h4:          { fontSize: 11, fontWeight: "600", color: BW.inkMuted, textTransform: "uppercase",
                 letterSpacing: 1.2, fontFamily: "monospace", marginTop: 16, marginBottom: 4 },
  p:           { fontSize: 14, lineHeight: 22, color: BW.ink, marginVertical: 6 },
  strong:      { fontWeight: "700", color: BW.ink },
  em:          { fontStyle: "italic", color: BW.ink },
  code:        { fontFamily: "monospace", fontSize: 12, color: BW.accent, backgroundColor: BW.accentSoft,
                 paddingHorizontal: 4, paddingVertical: 1 },
  mark:        { backgroundColor: "rgba(92, 140, 230, 0.28)", color: BW.ink },
  ins:         { color: BW.success },
  del:         { color: BW.inkMuted, textDecorationLine: "line-through" },
  a:           { color: BW.accent, textDecorationLine: "underline" },
  ul:          { marginVertical: 8 },
  li:          { flexDirection: "row", marginVertical: 3, alignItems: "flex-start" },
  liMarker:    { width: 18, color: BW.accent, fontWeight: "700", fontSize: 14, lineHeight: 22 },
  liContent:   { flex: 1 },
  hr:          { height: 1, backgroundColor: BW.border, marginVertical: 12 },
  blockquote:  { borderLeftWidth: 2, borderLeftColor: BW.accent, paddingHorizontal: 12, marginVertical: 10 },
});

// Class vocabulary — keys here must equal the set in
// infra/render/note_grammar.ALLOWED_CLASSES. Parity test enforces.
export const STYLES = StyleSheet.create({
  // containers
  "card":            { backgroundColor: BW.surface, borderWidth: 1, borderColor: BW.border, padding: 18, marginVertical: 8 },
  "card-hero":       { backgroundColor: BW.surface, borderWidth: 1, borderColor: BW.accent, padding: 18, marginVertical: 8 },
  "card-callout":    { borderLeftWidth: 3, borderLeftColor: BW.accent, paddingLeft: 14, marginVertical: 8 },
  "card-compare":    { flexDirection: "row", gap: 14 },
  "card-timeline":   { borderLeftWidth: 1, borderLeftColor: BW.borderStrong, paddingLeft: 20, marginVertical: 8 },
  "card-definition": { backgroundColor: BW.surface2, borderWidth: 1, borderColor: BW.border, padding: 18, marginVertical: 8 },
  "row":             { flexDirection: "row", flexWrap: "wrap" },
  "col":             { flexDirection: "column", flex: 1 },
  "gap-sm":          { gap: 6 },
  "gap-md":          { gap: 12 },
  "gap-lg":          { gap: 20 },
  "pad-sm":          { padding: 6 },
  "pad-md":          { padding: 12 },
  "pad-lg":          { padding: 20 },

  // tone / accent
  "accent":          { color: BW.accent },
  "accent-soft":     { color: BW.accentSoft },
  "muted":           { color: BW.inkMuted },
  "danger":          { color: BW.danger },
  "warn":            { color: BW.warn },
  "success":         { color: BW.success },
  "info":            { color: BW.info },
  "bg-surface":      { backgroundColor: BW.surface },
  "bg-surface-2":    { backgroundColor: BW.surface2 },
  "bg-accent-soft":  { backgroundColor: BW.accentSoft },

  // type scale
  "t-display":       { fontSize: 24, fontWeight: "800", letterSpacing: -0.6 },
  "t-title":         { fontSize: 18, fontWeight: "700", letterSpacing: -0.3 },
  "t-body":          { fontSize: 14, fontWeight: "400", lineHeight: 22 },
  "t-caption":       { fontSize: 11, fontWeight: "600", color: BW.inkMuted, fontFamily: "monospace",
                       textTransform: "uppercase", letterSpacing: 1.4 },
  "t-mono":          { fontFamily: "monospace", fontSize: 13 },
  "weight-bold":     { fontWeight: "700" },
  "weight-semi":     { fontWeight: "600" },
  "italic":          { fontStyle: "italic" },

  // annotation
  "revision-add":    { backgroundColor: BW.revisionAddBg, color: BW.revisionAddFg, paddingHorizontal: 4 },
  "revision-remove": { backgroundColor: BW.revisionRemBg, color: BW.revisionRemFg, paddingHorizontal: 4,
                       textDecorationLine: "line-through" },
  "revision-changed":{ backgroundColor: BW.accentSoft, color: BW.accent, paddingHorizontal: 4 },

  // media
  "bw-diagram":      { backgroundColor: BW.surface2, borderWidth: 1, borderColor: BW.border, padding: 8,
                       marginVertical: 12 },
  "bw-image":        { width: "100%", marginVertical: 10 },
  "math":           { marginVertical: 8 },
  "aspect-1-1":      { aspectRatio: 1 },
  "aspect-4-3":      { aspectRatio: 4 / 3 },
  "aspect-16-9":     { aspectRatio: 16 / 9 },
  "aspect-3-4":      { aspectRatio: 3 / 4 },

  // layout helpers
  "center":          { textAlign: "center" },
  "right":           { textAlign: "right" },
  "border":          { borderWidth: 1, borderColor: BW.border },
  "border-top":      { borderTopWidth: 1, borderTopColor: BW.border },
  "border-bottom":   { borderBottomWidth: 1, borderBottomColor: BW.border },
  "round":           { borderRadius: 4 },
  "round-lg":        { borderRadius: 8 },
});
