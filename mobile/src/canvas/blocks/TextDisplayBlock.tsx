// Read-only prose surface. Phase 2's first non-voice block. RN port of
// frontend/templates/blocks/text_display.js — header strip + scrollable body.
// Initial content arrives in params.content; later updates come over the
// per-block content topic `text.<block_id>.content` (push_block_content).
//
// Markdown rendering is deliberately deferred — RN doesn't have a built-in
// renderer and adding a dep this early is over-investment. Persona prose
// reads fine as plain text in P2; if the visual is too plain we add
// react-native-markdown-display later (TODO.md).

import React, { useEffect, useMemo, useRef, useState } from "react";
import { ScrollView, StyleSheet, Text, View } from "react-native";
import { bus } from "../../lib/bus/bus";
import type { BlockProps } from "../blockRegistry";

export function TextDisplayBlock({ blockId, params }: BlockProps): React.ReactElement {
  const initial = useMemo(() => {
    const c = params?.content;
    return typeof c === "string" ? c : "";
  }, [params]);

  const [text, setText] = useState(initial);
  const contentTopic = useMemo(() => `text.${blockId}.content`, [blockId]);

  useEffect(() => {
    const unsub = bus.subscribe(contentTopic, (payload: unknown) => {
      if (typeof payload === "string") {
        setText(payload);
      } else if (payload && typeof (payload as { content?: unknown }).content === "string") {
        setText((payload as { content: string }).content);
      }
    });
    return () => { unsub(); };
  }, [contentTopic]);

  // Keep latest text in a ref for the char-count meta without forcing a
  // re-render of the header on every keystroke if we ever expose editing.
  const lenRef = useRef(0);
  lenRef.current = text.trim().length;

  return (
    <View style={styles.container}>
      <View style={styles.header}>
        <Text style={styles.idChip}>NOTE</Text>
        <Text style={styles.title} numberOfLines={1}>Note</Text>
        <Text style={styles.meta}>{lenRef.current ? `${lenRef.current} chars` : "empty"}</Text>
      </View>
      <ScrollView style={styles.body} contentContainerStyle={styles.bodyContent}>
        <Text style={styles.prose} selectable>{text}</Text>
      </ScrollView>
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: "#11131a",
    borderWidth: 1,
    borderColor: "#22252e",
  },
  header: {
    flexDirection: "row",
    alignItems: "center",
    gap: 10,
    paddingHorizontal: 12,
    paddingVertical: 9,
    backgroundColor: "#161924",
    borderBottomWidth: 1,
    borderBottomColor: "#22252e",
  },
  idChip: {
    fontFamily: "monospace",
    fontSize: 9.5,
    color: "#7c8cf8",
    backgroundColor: "rgba(124,140,248,0.12)",
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
    color: "#e7e8ed",
  },
  meta: {
    fontFamily: "monospace",
    fontSize: 10,
    color: "#6b6f7d",
    textTransform: "uppercase",
    letterSpacing: 0.8,
  },
  body: {
    flex: 1,
  },
  bodyContent: {
    padding: 18,
    paddingHorizontal: 22,
  },
  prose: {
    color: "#cfd1d8",
    fontSize: 14,
    lineHeight: 22,
  },
});
