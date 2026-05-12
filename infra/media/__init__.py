"""Provider-agnostic media processing — ffmpeg-driven frame/audio extraction,
pHash dedup, VAD, and the video-understanding orchestrator that fans out to
the active vision provider per frame and to the transcribe sidecar per voiced
span.

Distinct from `infra/model/vision/`: that directory dispatches a single image
to a provider; this one orchestrates a video into N image calls + a transcript.
"""
