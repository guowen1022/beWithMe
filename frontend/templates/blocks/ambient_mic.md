---
keywords: mic microphone listen voice talk ambient speak
purpose: Always-on mic block. While mounted and unmuted, captures user speech via VAD and posts each transcribed phrase to the chosen persona as a perception event. Mount/unmount is the kill switch.
grid:
  x: 8
  y: 7
  w: 4
  h: 2
publishes:
  - ambient_mic.muted
  - ambient_mic.speak_to
backend:
  recordUtterance:
    method: POST
    path: /api/perception/utterance
    auth: user
    content_type: application/json
    returns: json
---

A small panel on the canvas with three controls:

  * a pulsing dot that flashes while a phrase is being captured,
  * a "Speak to" dropdown choosing which persona receives the utterance,
  * a mute toggle (mic stays mounted but stops capturing).

The panel uses `helpers.audio.startVad` (which calls `createMicVad` under
the hood and is gated by the shared `micArbiter`), `helpers.audio.transcribe`
(local Whisper), and `helpers.backend.recordUtterance` (POST to the
perception cache on the persona sidecar).

Talk is cheap. Nothing about ambient speech is recorded to formal memory:
the persona's perception cache holds the last ~50 utterances in RAM and
they vanish when the sidecar restarts.

The "Speak to" choice is per-device, persisted in `localStorage` under
the key `ambient_mic.speak_to`. On a fresh device the dropdown defaults
to `"teacher"`.

`autosnapshot: false` because the block's content is just three controls;
the teacher's `read_media` doesn't need to read it back.
