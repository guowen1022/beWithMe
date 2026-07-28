// Runtime config: base URL, user id, device id. Persisted in AsyncStorage,
// mirrored to memory so synchronous reads from request builders are cheap.

import AsyncStorage from "@react-native-async-storage/async-storage";

const KEY_BASE_URL = "bewithme.baseUrl";
const KEY_USER_ID = "bewithme.userId";
const KEY_DEVICE_ID = "bewithme.deviceId";
const KEY_OUTPUT_DEVICE_ID = "bewithme.outputDeviceId";
// Signed session token (docs/SECURITY.md). Null in legacy mode, where the
// backend has no token to issue and X-User-Id alone is accepted.
const KEY_SESSION_TOKEN = "bewithme.sessionToken";

interface ConfigState {
  baseUrl: string;
  userId: string | null;
  deviceId: string;
  outputDeviceId: string | null;
  sessionToken: string | null;
}

const state: ConfigState = {
  baseUrl: "",
  userId: null,
  deviceId: "",
  outputDeviceId: null,
  sessionToken: null,
};

let loaded = false;

export function uuidv4(): string {
  // RN doesn't ship crypto.randomUUID; roll a v4 with Math.random.
  // Good enough for a stable per-install device id — not used for security.
  const hex = (n: number) => Math.floor(Math.random() * 16 ** n).toString(16).padStart(n, "0");
  const a = hex(8);
  const b = hex(4);
  const c = (0x4000 | (Math.floor(Math.random() * 0x1000))).toString(16);
  const d = (0x8000 | (Math.floor(Math.random() * 0x4000))).toString(16);
  const e = hex(8) + hex(4);
  return `${a}-${b}-${c}-${d}-${e}`;
}

export async function loadConfig(): Promise<ConfigState> {
  if (loaded) return { ...state };
  const [baseUrl, userId, deviceId, outputDeviceId, sessionToken] = await Promise.all([
    AsyncStorage.getItem(KEY_BASE_URL),
    AsyncStorage.getItem(KEY_USER_ID),
    AsyncStorage.getItem(KEY_DEVICE_ID),
    AsyncStorage.getItem(KEY_OUTPUT_DEVICE_ID),
    AsyncStorage.getItem(KEY_SESSION_TOKEN),
  ]);
  state.baseUrl = baseUrl ?? "";
  state.userId = userId;
  state.outputDeviceId = outputDeviceId;
  state.sessionToken = sessionToken;
  if (deviceId) {
    state.deviceId = deviceId;
  } else {
    state.deviceId = uuidv4();
    await AsyncStorage.setItem(KEY_DEVICE_ID, state.deviceId);
  }
  loaded = true;
  return { ...state };
}

export function getBaseUrl(): string { return state.baseUrl; }
export function getUserId(): string | null { return state.userId; }
export function getDeviceId(): string { return state.deviceId; }
export function getOutputDeviceId(): string | null { return state.outputDeviceId; }
export function getSessionToken(): string | null { return state.sessionToken; }

export async function setBaseUrl(url: string): Promise<void> {
  state.baseUrl = url.replace(/\/+$/, "");
  await AsyncStorage.setItem(KEY_BASE_URL, state.baseUrl);
}

export async function setUserId(id: string): Promise<void> {
  state.userId = id;
  await AsyncStorage.setItem(KEY_USER_ID, id);
}

export async function setSessionToken(token: string): Promise<void> {
  state.sessionToken = token;
  await AsyncStorage.setItem(KEY_SESSION_TOKEN, token);
}

export async function clearUserId(): Promise<void> {
  state.userId = null;
  state.sessionToken = null;
  await Promise.all([
    AsyncStorage.removeItem(KEY_USER_ID),
    // Drop the token with the user -- a stale token for a signed-out identity
    // is exactly the thing that should not linger on a device.
    AsyncStorage.removeItem(KEY_SESSION_TOKEN),
  ]);
}

export async function setOutputDeviceId(id: string | null): Promise<void> {
  state.outputDeviceId = id;
  if (id) await AsyncStorage.setItem(KEY_OUTPUT_DEVICE_ID, id);
  else await AsyncStorage.removeItem(KEY_OUTPUT_DEVICE_ID);
}
