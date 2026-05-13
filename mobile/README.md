# beWithMe mobile

React Native (Expo) client for beWithMe. Phase 1: canvas with one block, the voice control. Listen and talk, that's it.

See `/Users/weng/.claude/plans/let-s-plan-a-big-enchanted-phoenix.md` for the full architecture plan.

## Setup — physical Android device

```bash
cd mobile
npm install
npx expo prebuild --platform android
npx expo run:android --device
```

The phone must be on the same LAN as the Mac running `./scripts/dev-services.sh`. Configure the backend URL on first launch via long-press settings.

## Setup — Android Emulator (no phone needed)

No Apple ID, no Xcode. Native ARM64 emulator on Apple Silicon. One-time deps:
`brew install android-commandlinetools openjdk` then install the emulator + an
ARM64 system image via `sdkmanager`. See `scripts/dev-mobile-sim.sh` for the
exact package names.

```bash
# from project root — creates AVD if needed, boots, builds, installs, launches:
./scripts/dev-mobile-sim.sh

# pick a different AVD:
AVD_NAME=Pixel_7_API_35 ./scripts/dev-mobile-sim.sh
```

The Android Emulator does **not** share the Mac's `localhost`. To reach the backend, point the app's baseUrl to `http://10.0.2.2:8000` (special "host loopback" alias). `./scripts/sim.sh ip` returns this address.

### Driving the emulator from the CLI

`scripts/sim.sh` wraps `adb` for the running emulator:

```bash
./scripts/sim.sh shot /tmp/sim.png      # screenshot
./scripts/sim.sh tap 540 1200           # tap at coordinates
./scripts/sim.sh swipe 100 800 100 200  # swipe gesture
./scripts/sim.sh type "hello world"     # type into focused field
./scripts/sim.sh back / home            # nav keys
./scripts/sim.sh url bewithme://...     # deep link / intent
./scripts/sim.sh launch                 # (re)launch the app
./scripts/sim.sh terminate              # force-stop
./scripts/sim.sh logs com.bewithme...   # tail logcat
./scripts/sim.sh devtools / reload      # RN dev menu / JS reload
./scripts/sim.sh stop                   # shutdown emulator
./scripts/sim.sh ip                     # 10.0.2.2 (host loopback)
```

## Architecture

Canvas-first. `DynamicSurface` renders a 4×9 grid; blocks are registered in `blockRegistry`. Phase 1 has one block (`ambient_mic`) which fills the canvas. The wire protocol matches desktop exactly — all traffic to shell:8000.
