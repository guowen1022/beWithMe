#!/usr/bin/env bash
# Boot the Android Emulator, build the mobile app, install, launch.
#
# Usage:
#   ./scripts/dev-mobile-sim.sh                  # boots default AVD
#   AVD_NAME=Pixel_7_API_35 ./scripts/dev-mobile-sim.sh
#   SKIP_BUILD=1 ./scripts/dev-mobile-sim.sh     # boot+launch, no rebuild
#
# Why Android (not iOS):
#   No Apple ID / Xcode required. Native ARM64 emulator on Apple Silicon.
#   adb gives us scriptable input.tap, input.text, screencap, logcat.
#
# Networking note:
#   Android Emulator does NOT share the Mac's localhost. To reach the backend
#   on the Mac, point the app's baseUrl to http://10.0.2.2:8000 (special alias
#   for "host loopback"). `./scripts/sim.sh ip` returns this address.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ANDROID_HOME="${ANDROID_HOME:-$HOME/Library/Android/sdk}"
# RN Gradle plugin requires JDK 17 (not 21/25). Force it explicitly.
JAVA_HOME="${JAVA_HOME:-/opt/homebrew/opt/openjdk@17/libexec/openjdk.jdk/Contents/Home}"
AVD_NAME="${AVD_NAME:-bewithme_pixel_35}"
SYSTEM_IMAGE="${SYSTEM_IMAGE:-system-images;android-35;google_apis_playstore;arm64-v8a}"
BUNDLE_ID="com.bewithme.mobile"

export ANDROID_HOME ANDROID_SDK_ROOT="$ANDROID_HOME" JAVA_HOME
export PATH="$JAVA_HOME/bin:$ANDROID_HOME/emulator:$ANDROID_HOME/platform-tools:$PATH"

if ! command -v emulator >/dev/null 2>&1; then
  echo "[sim] 'emulator' not on PATH — install with:" >&2
  echo "  sdkmanager --sdk_root=\"\$ANDROID_HOME\" emulator \"$SYSTEM_IMAGE\"" >&2
  exit 1
fi

if ! /opt/homebrew/bin/avdmanager list avd 2>/dev/null | grep -q "Name: ${AVD_NAME}\b"; then
  echo "[sim] creating AVD '$AVD_NAME' from $SYSTEM_IMAGE..."
  echo "no" | /opt/homebrew/bin/avdmanager create avd \
    --name "$AVD_NAME" \
    --package "$SYSTEM_IMAGE" \
    --device "pixel_7" 2>&1 | tail -3
fi

if ! adb devices | grep -q "emulator-.*device$"; then
  echo "[sim] booting $AVD_NAME (15-30 sec first time)..."
  nohup emulator -avd "$AVD_NAME" -no-snapshot-save -gpu auto >/tmp/emulator.log 2>&1 &
  echo "[sim] waiting for device..."
  adb wait-for-device
  # Wait for the boot animation to finish
  for i in $(seq 1 90); do
    booted=$(adb shell getprop sys.boot_completed 2>/dev/null | tr -d '\r')
    [[ "$booted" == "1" ]] && break
    sleep 2
  done
  [[ "$booted" == "1" ]] || { echo "[sim] timed out waiting for boot" >&2; exit 1; }
  echo "[sim] device ready"
fi

if [[ "${SKIP_BUILD:-0}" != "1" ]]; then
  echo "[sim] building app (first time: 3-7 min)..."
  cd "$ROOT/mobile"
  npx expo run:android
  cd "$ROOT"
else
  echo "[sim] launching existing $BUNDLE_ID"
  adb shell monkey -p "$BUNDLE_ID" -c android.intent.category.LAUNCHER 1 >/dev/null 2>&1 || true
fi

echo "[sim] ready. Useful commands:"
echo "  ./scripts/sim.sh shot /tmp/sim.png        # screenshot"
echo "  ./scripts/sim.sh tap X Y                  # tap at coords"
echo "  ./scripts/sim.sh type \"hello\"             # type text"
echo "  ./scripts/sim.sh url <url>                # open deep link"
echo "  ./scripts/sim.sh logs                     # tail logcat"
echo "  ./scripts/sim.sh stop                     # shutdown emulator"
echo "  Backend baseUrl for app: http://10.0.2.2:8000"
