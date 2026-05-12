"""Voice benchmark scenarios.

Each scenario is a list of short user utterances. Short on purpose — long
audio inflates STT time and obscures the rest of the pipeline. We are
measuring response latency, not summarization quality.
"""

SCENARIO_QUICK = {
    "name": "quick_questions",
    "profile": (
        "I'm a curious learner who likes short, conversational explanations."
    ),
    "talk_preference": {"desktop": "voice", "tablet": "voice", "phone": "voice"},
    "questions": [
        "Hi, what can you help me with today?",
        "What is a mitochondria?",
        "Why does that matter for energy?",
        "Tell me a quick analogy for it.",
        "Got it, thanks.",
    ],
}


ALL_SCENARIOS = [SCENARIO_QUICK]
