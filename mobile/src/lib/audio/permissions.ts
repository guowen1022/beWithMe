import { PermissionsAndroid, Platform } from "react-native";

export async function ensureMicPermission(): Promise<boolean> {
  if (Platform.OS !== "android") return true;
  const granted = await PermissionsAndroid.check(PermissionsAndroid.PERMISSIONS.RECORD_AUDIO);
  if (granted) return true;
  const result = await PermissionsAndroid.request(PermissionsAndroid.PERMISSIONS.RECORD_AUDIO, {
    title: "beWithMe needs the microphone",
    message: "To listen and talk, beWithMe needs to use the mic.",
    buttonPositive: "OK",
  });
  return result === PermissionsAndroid.RESULTS.GRANTED;
}
