#!/usr/bin/env bash
# Short verb wrapper around `adb` for driving the running Android emulator.
#
#   sim shot [path]                screenshot to path (default /tmp/sim.png)
#   sim tap X Y                    tap at screen coordinates
#   sim swipe X1 Y1 X2 Y2 [ms]     swipe gesture
#   sim type "text"                type text into focused field
#   sim key <code>                 send keyevent (e.g. KEYCODE_BACK, 4=back, 3=home)
#   sim back                       press back
#   sim home                       go to launcher
#   sim url <url>                  open URL / deep link
#   sim launch [bundle]            launch app (default com.bewithme.mobile)
#   sim terminate [bundle]         force-stop app
#   sim logs [bundle]              tail logcat (filter to bundle if given)
#   sim devtools                   open RN dev menu
#   sim reload                     reload JS bundle
#   sim stop                       shutdown the running emulator
#   sim ip                         host loopback addr for the app's baseUrl

set -euo pipefail

ANDROID_HOME="${ANDROID_HOME:-$HOME/Library/Android/sdk}"
export PATH="$ANDROID_HOME/platform-tools:$ANDROID_HOME/emulator:$PATH"
BUNDLE_DEFAULT="com.bewithme.mobile"

require_device() {
  if ! adb devices | grep -q "emulator-.*device$"; then
    echo "no emulator running. start one with ./scripts/dev-mobile-sim.sh" >&2
    exit 1
  fi
}

cmd="${1:-}"
shift || true

case "$cmd" in
  shot)
    require_device
    out="${1:-/tmp/sim.png}"
    adb exec-out screencap -p > "$out"
    echo "$out"
    ;;
  tap)
    require_device
    [[ -z "${1:-}" || -z "${2:-}" ]] && { echo "usage: sim tap X Y" >&2; exit 1; }
    adb shell input tap "$1" "$2"
    ;;
  swipe)
    require_device
    [[ -z "${4:-}" ]] && { echo "usage: sim swipe X1 Y1 X2 Y2 [duration_ms]" >&2; exit 1; }
    adb shell input swipe "$1" "$2" "$3" "$4" "${5:-300}"
    ;;
  type)
    require_device
    [[ -z "${1:-}" ]] && { echo "usage: sim type \"text\"" >&2; exit 1; }
    # spaces → %s; adb input text doesn't accept literal spaces
    adb shell input text "${1// /%s}"
    ;;
  key)
    require_device
    [[ -z "${1:-}" ]] && { echo "usage: sim key <keycode>" >&2; exit 1; }
    adb shell input keyevent "$1"
    ;;
  back)
    require_device
    adb shell input keyevent 4
    ;;
  home)
    require_device
    adb shell input keyevent 3
    ;;
  url)
    require_device
    [[ -z "${1:-}" ]] && { echo "usage: sim url <url>" >&2; exit 1; }
    adb shell am start -a android.intent.action.VIEW -d "$1"
    ;;
  launch)
    require_device
    bundle="${1:-$BUNDLE_DEFAULT}"
    adb shell monkey -p "$bundle" -c android.intent.category.LAUNCHER 1 >/dev/null
    ;;
  terminate)
    require_device
    bundle="${1:-$BUNDLE_DEFAULT}"
    adb shell am force-stop "$bundle"
    ;;
  logs)
    require_device
    bundle="${1:-}"
    if [[ -n "$bundle" ]]; then
      pid=$(adb shell pidof "$bundle" | tr -d '\r')
      [[ -z "$pid" ]] && { echo "$bundle not running" >&2; exit 1; }
      adb logcat --pid="$pid"
    else
      adb logcat -v time
    fi
    ;;
  devtools)
    require_device
    # RN dev menu keystroke
    adb shell input keyevent 82
    ;;
  reload)
    require_device
    # Cmd+R equivalent — press R twice
    adb shell input keyevent KEYCODE_R
    adb shell input keyevent KEYCODE_R
    ;;
  stop)
    require_device
    adb emu kill
    ;;
  ip)
    # special host-loopback alias inside the emulator
    echo "10.0.2.2"
    ;;
  *)
    grep -E '^#( |  )' "$0" | sed 's/^#//'
    exit 1
    ;;
esac
