import React, { useCallback, useEffect, useState } from "react";
import { Alert, Pressable, ScrollView, StyleSheet, Text, TextInput, View } from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { useNavigation } from "@react-navigation/native";
import {
  clearUserId, getBaseUrl, getUserId, setBaseUrl, setUserId,
  getOutputDeviceId, setOutputDeviceId, getDeviceId,
} from "../config";
import { ensureUser } from "../lib/api/users";
import { startSession } from "../lib/api/client";
import { updateTalkPreference, PHONE_VOICE_PREFERENCE } from "../lib/api/profile";
import { listDevices, type DeviceSummary } from "../lib/api/devices";

// Access key for a backend running BEWITHME_AUTH_MODE=strict. Undefined for
// private/legacy deployments, which need no credential — which is why signing
// in looks identical either way.
const BEWITHME_ACCESS_KEY = process.env.EXPO_PUBLIC_BEWITHME_ACCESS_KEY;

export function SettingsScreen(): React.ReactElement {
  const nav = useNavigation();
  const [baseUrl, setBaseUrlState] = useState("");
  const [username, setUsername] = useState("");
  const [userId, setUserIdState] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [outputDeviceId, setOutputState] = useState<string | null>(null);
  const [devices, setDevices] = useState<DeviceSummary[]>([]);
  const [devicesLoading, setDevicesLoading] = useState(false);

  const refreshDevices = useCallback(async () => {
    if (!getUserId() || !getBaseUrl()) return;
    setDevicesLoading(true);
    try {
      const list = await listDevices();
      // Hide self.
      const selfId = getDeviceId();
      setDevices(list.filter((d) => d.id !== selfId));
    } catch (e) {
      console.warn("[settings] listDevices failed:", e);
    } finally {
      setDevicesLoading(false);
    }
  }, []);

  useEffect(() => {
    setBaseUrlState(getBaseUrl());
    setUserIdState(getUserId());
    setOutputState(getOutputDeviceId());
    void refreshDevices();
  }, [refreshDevices]);

  async function pickOutputDevice(id: string | null) {
    await setOutputDeviceId(id);
    setOutputState(id);
  }

  async function saveBaseUrl() {
    try {
      await setBaseUrl(baseUrl.trim());
      Alert.alert("Saved", "Backend URL updated.");
    } catch (e) {
      Alert.alert("Error", String(e));
    }
  }

  async function bootstrapUser(uname?: string) {
    const name = (uname ?? username).trim();
    if (!name) { Alert.alert("Username required"); return; }
    if (!getBaseUrl()) { Alert.alert("Set backend URL first"); return; }
    setBusy(true);
    try {
      const user = await ensureUser(name);
      await setUserId(user.id);
      // Exchange for a signed session token before any authenticated call.
      // No-op against a legacy backend, which issues none. docs/SECURITY.md.
      await startSession(user.id, BEWITHME_ACCESS_KEY);
      await updateTalkPreference(PHONE_VOICE_PREFERENCE).catch(() => {
        // Non-fatal: persona will still answer, just without Lane A voice prompt.
        console.warn("[settings] talk-preference update failed");
      });
      setUserIdState(user.id);
      void refreshDevices();
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
        <Pressable style={[styles.button, busy && styles.buttonDisabled]} disabled={busy} onPress={() => bootstrapUser()}>
          <Text style={styles.buttonText}>{busy ? "..." : "Create / Sign in"}</Text>
        </Pressable>
        <Pressable
          style={[styles.button, styles.shortcut, busy && styles.buttonDisabled]}
          disabled={busy}
          onPress={() => bootstrapUser("default")}
        >
          <Text style={styles.buttonText}>Sign in as default</Text>
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

      <View style={styles.row}>
        <View style={styles.deviceHeader}>
          <Text style={styles.label}>Output device</Text>
          <Pressable hitSlop={8} onPress={refreshDevices}>
            <Text style={styles.refresh}>{devicesLoading ? "..." : "Refresh"}</Text>
          </Pressable>
        </View>
        <Text style={styles.hint}>Where the answer (voice + UI) lands. Local = this phone.</Text>
        <ScrollView style={styles.deviceList} nestedScrollEnabled>
          <DeviceRow
            label="Local (this phone)"
            sub="play TTS through the phone speaker"
            selected={outputDeviceId === null}
            onPress={() => pickOutputDevice(null)}
          />
          {devices.map((d) => (
            <DeviceRow
              key={d.id}
              label={`${d.device_class} · ${d.id.slice(0, 8)}`}
              sub={d.online ? "online" : (d.last_seen ? `last seen ${new Date(d.last_seen).toLocaleString()}` : "offline")}
              selected={outputDeviceId === d.id}
              dim={!d.online}
              onPress={() => pickOutputDevice(d.id)}
            />
          ))}
        </ScrollView>
      </View>

      <Pressable style={[styles.button, styles.dismiss]} onPress={() => nav.goBack()}>
        <Text style={styles.buttonText}>Close</Text>
      </Pressable>
    </SafeAreaView>
  );
}

function DeviceRow(props: {
  label: string; sub: string; selected: boolean; dim?: boolean; onPress: () => void;
}): React.ReactElement {
  return (
    <Pressable onPress={props.onPress} style={[styles.deviceRow, props.selected && styles.deviceRowSelected, props.dim && styles.deviceRowDim]}>
      <View style={styles.deviceDot}>
        {props.selected ? <View style={styles.deviceDotInner} /> : null}
      </View>
      <View style={{ flex: 1 }}>
        <Text style={styles.deviceLabel}>{props.label}</Text>
        <Text style={styles.deviceSub}>{props.sub}</Text>
      </View>
    </Pressable>
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
  shortcut: { marginTop: 8, backgroundColor: "#7c8cf8" },
  dismiss: { marginTop: 32, backgroundColor: "#27272a" },
  deviceHeader: { flexDirection: "row", alignItems: "center", justifyContent: "space-between", marginBottom: 4 },
  refresh: { color: "#7c8cf8", fontSize: 12, textTransform: "uppercase", letterSpacing: 1 },
  hint: { color: "#666", fontSize: 11, marginBottom: 10 },
  deviceList: { maxHeight: 240 },
  deviceRow: {
    flexDirection: "row", alignItems: "center", gap: 12,
    padding: 12, borderRadius: 8, marginBottom: 6,
    backgroundColor: "#16161e",
  },
  deviceRowSelected: { borderWidth: 1, borderColor: "#7c8cf8" },
  deviceRowDim: { opacity: 0.55 },
  deviceDot: {
    width: 18, height: 18, borderRadius: 9,
    borderWidth: 1, borderColor: "#444",
    alignItems: "center", justifyContent: "center",
  },
  deviceDotInner: { width: 10, height: 10, borderRadius: 5, backgroundColor: "#7c8cf8" },
  deviceLabel: { color: "#ddd", fontSize: 13 },
  deviceSub: { color: "#777", fontSize: 11, marginTop: 2 },
});
