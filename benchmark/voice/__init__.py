"""Voice-to-voice persona response-time benchmark.

Measures the perceived-latency critical path of the assistant: from the
user finishing their utterance to the moment audio playback would begin.
Headless — exercises the backend pipeline (transcribe → ask → speak) only.
"""
