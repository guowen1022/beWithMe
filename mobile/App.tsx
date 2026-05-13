import React, { useEffect, useState } from "react";
import { ActivityIndicator, StyleSheet, View } from "react-native";
import { NavigationContainer } from "@react-navigation/native";
import { SafeAreaProvider } from "react-native-safe-area-context";
import { StatusBar } from "expo-status-bar";
import { loadConfig } from "./src/config";
import { RootNavigator } from "./src/navigation/RootNavigator";
import { warmSilero } from "./src/lib/vad/sileroVad";

const NAV_THEME = {
  dark: true,
  colors: {
    primary: "#22c55e",
    background: "#0a0a0f",
    card: "#0a0a0f",
    text: "#fafafa",
    border: "#27272a",
    notification: "#ef4444",
  },
  // RN Navigation v7 needs `fonts` on the theme object.
  fonts: {
    regular: { fontFamily: "System", fontWeight: "400" as const },
    medium: { fontFamily: "System", fontWeight: "500" as const },
    bold: { fontFamily: "System", fontWeight: "700" as const },
    heavy: { fontFamily: "System", fontWeight: "900" as const },
  },
};

export default function App(): React.ReactElement {
  const [ready, setReady] = useState(false);

  useEffect(() => {
    loadConfig().then(() => setReady(true)).catch((e) => {
      console.warn("[App] loadConfig failed:", e);
      setReady(true);
    });
    // Eager-load the silero VAD model so the first ambient toggle isn't
    // gated on the 200-500 ms ONNX session create. Failure is non-fatal —
    // ambient mode will surface the error then.
    warmSilero().catch((e) => console.warn("[App] warmSilero failed:", e));
  }, []);

  if (!ready) {
    return (
      <View style={styles.boot}>
        <ActivityIndicator color="#22c55e" />
      </View>
    );
  }

  return (
    <SafeAreaProvider>
      <NavigationContainer theme={NAV_THEME}>
        <StatusBar style="light" />
        <RootNavigator />
      </NavigationContainer>
    </SafeAreaProvider>
  );
}

const styles = StyleSheet.create({
  boot: {
    flex: 1,
    alignItems: "center",
    justifyContent: "center",
    backgroundColor: "#0a0a0f",
  },
});
