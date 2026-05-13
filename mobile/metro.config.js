// Expo's default Metro config + bundle .onnx files as assets so the silero
// VAD model ships inside the APK and `Asset.fromModule(require(...))` can
// resolve its on-device path at runtime.
const { getDefaultConfig } = require("expo/metro-config");

const config = getDefaultConfig(__dirname);
config.resolver.assetExts = [...(config.resolver.assetExts ?? []), "onnx"];

module.exports = config;
