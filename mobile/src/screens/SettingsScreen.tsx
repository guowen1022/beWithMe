import React, { useEffect, useState } from "react";
import { Alert, Pressable, StyleSheet, Text, TextInput, View } from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { useNavigation } from "@react-navigation/native";
import { clearUserId, getBaseUrl, getUserId, setBaseUrl, setUserId } from "../config";
import { createUser } from "../lib/api/users";
import { updateTalkPreference, PHONE_VOICE_PREFERENCE } from "../lib/api/profile";

export function SettingsScreen(): React.ReactElement {
  const nav = useNavigation();
  const [baseUrl, setBaseUrlState] = useState("");
  const [username, setUsername] = useState("");
  const [userId, setUserIdState] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    setBaseUrlState(getBaseUrl());
    setUserIdState(getUserId());
  }, []);

  async function saveBaseUrl() {
    try {
      await setBaseUrl(baseUrl.trim());
      Alert.alert("Saved", "Backend URL updated.");
    } catch (e) {
      Alert.alert("Error", String(e));
    }
  }

  async function bootstrapUser() {
    if (!username.trim()) { Alert.alert("Username required"); return; }
    if (!getBaseUrl()) { Alert.alert("Set backend URL first"); return; }
    setBusy(true);
    try {
      const user = await createUser(username.trim());
      await setUserId(user.id);
      await updateTalkPreference(PHONE_VOICE_PREFERENCE).catch(() => {
        // Non-fatal: persona will still answer, just without Lane A voice prompt.
        console.warn("[settings] talk-preference update failed");
      });
      setUserIdState(user.id);
      Alert.alert("Welcome", `Signed in as ${user.username}`);
    } catch (e) {
      Alert.alert("Error", String(e));
    } finally {
      setBusy(false);
    }
  }

  async function reset() {
    await clearUserId();
    setUserIdState(null);
    Alert.alert("Reset", "Signed out on this device.");
  }

  return (
    <SafeAreaView style={styles.root}>
      <View style={styles.row}>
        <Text style={styles.label}>Backend URL</Text>
        <TextInput
          value={baseUrl}
          onChangeText={setBaseUrlState}
          placeholder="http://192.168.x.x:8000"
          placeholderTextColor="#666"
          autoCapitalize="none"
          autoCorrect={false}
          style={styles.input}
        />
        <Pressable style={styles.button} onPress={saveBaseUrl}>
          <Text style={styles.buttonText}>Save URL</Text>
        </Pressable>
      </View>

      <View style={styles.row}>
        <Text style={styles.label}>Username</Text>
        <TextInput
          value={username}
          onChangeText={setUsername}
          placeholder={userId ? "(signed in)" : "your name"}
          placeholderTextColor="#666"
          autoCapitalize="none"
          autoCorrect={false}
          style={styles.input}
        />
        <Pressable style={[styles.button, busy && styles.buttonDisabled]} disabled={busy} onPress={bootstrapUser}>
          <Text style={styles.buttonText}>{busy ? "..." : "Create / Sign in"}</Text>
        </Pressable>
      </View>

      <View style={styles.row}>
        <Text style={styles.label}>Signed in</Text>
        <Text style={styles.value}>{userId ?? "—"}</Text>
        {userId && (
          <Pressable style={styles.button} onPress={reset}>
            <Text style={styles.buttonText}>Reset</Text>
          </Pressable>
        )}
      </View>

      <Pressable style={[styles.button, styles.dismiss]} onPress={() => nav.goBack()}>
        <Text style={styles.buttonText}>Close</Text>
      </Pressable>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: "#0a0a0f", padding: 24 },
  row: { marginVertical: 16 },
  label: { color: "#888", fontSize: 12, marginBottom: 6, letterSpacing: 1, textTransform: "uppercase" },
  value: { color: "#ddd", fontSize: 14, fontFamily: "monospace", marginBottom: 6 },
  input: {
    backgroundColor: "#16161e",
    color: "#fafafa",
    paddingHorizontal: 12,
    paddingVertical: 10,
    borderRadius: 8,
    fontSize: 16,
    marginBottom: 8,
  },
  button: {
    backgroundColor: "#22c55e",
    paddingVertical: 12,
    borderRadius: 8,
    alignItems: "center",
  },
  buttonDisabled: { opacity: 0.5 },
  buttonText: { color: "#0a0a0f", fontWeight: "600", fontSize: 14 },
  dismiss: { marginTop: 32, backgroundColor: "#27272a" },
});
