import React, { useEffect } from "react";
import { StyleSheet, View } from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { useNavigation } from "@react-navigation/native";
import type { NativeStackNavigationProp } from "@react-navigation/native-stack";
import { DynamicSurface } from "../canvas/DynamicSurface";
import { getBaseUrl, getUserId } from "../config";
import type { RootStackParamList } from "../navigation/RootNavigator";

export function CanvasScreen(): React.ReactElement {
  const nav = useNavigation<NativeStackNavigationProp<RootStackParamList>>();

  useEffect(() => {
    // First-launch nudge: send the user to Settings if either the backend
    // URL or the user id is missing. Long-press on the dot is the normal
    // way in; on first launch that's not discoverable yet.
    if (!getBaseUrl() || !getUserId()) {
      const t = setTimeout(() => nav.navigate("Settings"), 250);
      return () => clearTimeout(t);
    }
    return undefined;
  }, [nav]);

  return (
    <SafeAreaView style={styles.root} edges={["top", "bottom"]}>
      <View style={styles.fill}>
        <DynamicSurface />
      </View>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: "#0a0a0f" },
  fill: { flex: 1 },
});
