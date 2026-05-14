// Teacher's primary explanation surface on mobile. Mirrors the web
// block at frontend/templates/blocks/note.js — same content topic
// (`text.<id>.content`), same HTML grammar, but rendered through
// native View/Text/Image/SvgXml instead of innerHTML. Backend sanitizes
// + pre-renders Mermaid SVG before either surface sees it, so this
// component never touches raw persona HTML.

import React, { useEffect, useMemo, useState } from "react";
import { ScrollView, StyleSheet, Text, View } from "react-native";

import { bus } from "../../lib/bus/bus";
import type { BlockProps } from "../blockRegistry";
import { renderNoteHtml } from "./noteMapper";

export function NoteBlock({ blockId, params }: BlockProps): React.ReactElement {
  const initial = useMemo(() => {
    const c = params?.content;
    return typeof c === "string" ? c : "";
  }, [params]);

  const [html, setHtml] = useState(initial);
  const contentTopic = useMemo(() => `text.${blockId}.content`, [blockId]);

  useEffect(() => {
    const unsub = bus.subscribe(contentTopic, (payload: unknown) => {
      if (typeof payload === "string") {
        setHtml(payload);
      } else if (payload && typeof (payload as { content?: unknown }).content === "string") {
        setHtml((payload as { content: string }).content);
      }
    });
    return () => { unsub(); };
  }, [contentTopic]);

  const rendered = useMemo(() => renderNoteHtml(html), [html]);

  // Header meta — count diagrams/images in the rendered tree by
  // scanning the source HTML (cheap, doesn't need to walk the React
  // tree). Stays in sync with what the user actually sees because
  // renderNoteHtml is deterministic on the same HTML.
  const counts = useMemo(() => {
    if (!html) return { chars: 0, diagrams: 0, images: 0 };
    const diagrams = (html.match(/<div[^>]*\bbw-diagram\b/g) || []).length;
    const images = (html.match(/<img\b/g) || []).length;
    const chars = html.replace(/<[^>]*>/g, "").trim().length;
    return { chars, diagrams, images };
  }, [html]);

  const metaText = counts.chars
    ? `${counts.chars} chars${counts.diagrams ? ` · ${counts.diagrams} diag` : ""}${counts.images ? ` · ${counts.images} img` : ""}`
    : "empty";

  return (
    <View style={styles.container}>
      <View style={styles.header}>
        <Text style={styles.idChip}>CARD</Text>
        <Text style={styles.title} numberOfLines={1}>Explanation</Text>
        <Text style={styles.meta}>{metaText}</Text>
      </View>
      <ScrollView style={styles.body} contentContainerStyle={styles.bodyContent}>
        {rendered}
      </ScrollView>
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: "#12121E",
    borderWidth: 1,
    borderColor: "#262638",
  },
  header: {
    flexDirection: "row",
    alignItems: "center",
    gap: 10,
    paddingHorizontal: 12,
    paddingVertical: 9,
    backgroundColor: "#1A1A2A",
    borderBottomWidth: 1,
    borderBottomColor: "#262638",
  },
  idChip: {
    fontFamily: "monospace",
    fontSize: 9.5,
    color: "#5C8CE6",
    backgroundColor: "rgba(92,140,230,0.16)",
    paddingHorizontal: 8,
    paddingVertical: 3,
    letterSpacing: 0.8,
    textTransform: "uppercase",
    overflow: "hidden",
  },
  title: {
    flex: 1,
    fontSize: 11.5,
    fontWeight: "600",
    color: "#F4F4F8",
  },
  meta: {
    fontFamily: "monospace",
    fontSize: 10,
    color: "#4F4F66",
    textTransform: "uppercase",
    letterSpacing: 0.8,
  },
  body: { flex: 1 },
  bodyContent: { paddingHorizontal: 22, paddingVertical: 18 },
});
