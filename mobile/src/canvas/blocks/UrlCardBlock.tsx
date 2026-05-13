// Compact perception card for a URL the persona silently read via read_url.
// RN port of frontend/templates/blocks/url_card.js — title + 1-line excerpt
// + host chip. Tap opens the URL in the system browser.

import React, { useCallback, useMemo } from "react";
import { Linking, Pressable, StyleSheet, Text, View } from "react-native";
import type { BlockProps } from "../blockRegistry";

function hostOf(u: string): string {
  try {
    return new URL(u).host || u;
  } catch {
    return u || "";
  }
}

export function UrlCardBlock({ params }: BlockProps): React.ReactElement {
  const url = typeof params?.url === "string" ? (params.url as string) : "";
  const title = typeof params?.title === "string" ? (params.title as string) : "";
  const excerpt = typeof params?.excerpt === "string" ? (params.excerpt as string) : "";

  const host = useMemo(() => hostOf(url), [url]);
  const displayTitle = title || url || "(no title)";

  const openUrl = useCallback(() => {
    if (!url) return;
    Linking.openURL(url).catch((err) => console.warn("[url_card] open failed:", err));
  }, [url]);

  return (
    <Pressable onPress={openUrl} style={styles.container}>
      <View style={styles.header}>
        <Text style={styles.idChip}>READ</Text>
        <Text style={styles.title} numberOfLines={1}>{displayTitle}</Text>
        <Text style={styles.host} numberOfLines={1}>{host}</Text>
      </View>
      {excerpt ? (
        <Text style={styles.excerpt} numberOfLines={2}>{excerpt}</Text>
      ) : null}
    </Pressable>
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
    fontSize: 12,
    fontWeight: "600",
    color: "#e7e8ed",
  },
  host: {
    fontFamily: "monospace",
    fontSize: 10,
    color: "#6b6f7d",
    maxWidth: "40%",
  },
  excerpt: {
    flex: 1,
    paddingHorizontal: 14,
    paddingVertical: 10,
    fontSize: 12,
    lineHeight: 18,
    color: "#9ea0ac",
  },
});
