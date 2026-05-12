"""Research-mode benchmark scenarios.

Each scenario specifies the URL the user is on, the question they
asked, and an explicit rubric for grading the agent's output:

  - `expected_procedure_keywords` — sets of substrings that, between
    them, should be touched by the agent's plan steps. The grader
    counts how many sets are matched anywhere in the plan; matching
    ALL sets is full credit, partial matches are partial credit.

  - `expected_result_keywords` — sets of substrings the synthesis
    should mention. Same counting rule.

  - `expected_min_tool_calls` — a soft floor on activity. A plan with
    no follow-through (research_plan + immediate speak with zero
    research_note) is a failure even if the synthesis happens to
    sound right.

Match counting is case-insensitive and substring-based ("photosynth"
matches "photosynthesis"). Use SETS of alternatives for steps that
could be expressed many ways.
"""

SCENARIO_1 = {
    "id": 1,
    "name": "Wikipedia: Photosynthesis — definition + surprising fact",
    "url": "https://en.wikipedia.org/wiki/Photosynthesis",
    "goal": (
        "What is photosynthesis, and what's the most interesting / surprising "
        "fact you can pull from this Wikipedia page?"
    ),
    # The plan should cover, between its steps, roughly:
    #   1) read / understand the page
    #   2) extract a definition / overview
    #   3) hunt for a surprising fact / number / record
    #   4) synthesize
    "expected_procedure_keywords": [
        ["read", "scan", "skim"],                         # acquire the text
        ["definition", "overview", "what", "process"],     # extract definition
        ["surprising", "interesting", "fact", "record",
         "stat", "number"],                                # find the hook
        ["synthesi", "summari", "answer", "combine"],      # close the loop
    ],
    # The synthesis should describe photosynthesis AND mention at least one
    # concrete page fact.
    "expected_result_keywords": [
        ["photosynth"],                                    # name the topic
        ["light", "sunlight", "chlorophyll"],              # mechanism keyword
        ["co2", "carbon", "oxygen", "water", "glucose",
         "sugar", "atp", "calvin"],                        # chemistry keyword
        ["tw", "terawatt", "global", "earth", "1779",
         "1771", "ingenhousz", "priestley", "year",
         "billion", "%", "percent"],                       # specific page fact
    ],
    "expected_min_tool_calls": 3,
    "deadline_target_s": 90.0,
}

SCENARIO_2 = {
    "id": 2,
    "name": "Wikipedia: HTTP/2 — what / why / main innovation vs HTTP/1.1",
    "url": "https://en.wikipedia.org/wiki/HTTP/2",
    "goal": (
        "What is HTTP/2, what problem does it solve compared to HTTP/1.1, "
        "and what's its main innovation? Quote the specific mechanism."
    ),
    "expected_procedure_keywords": [
        ["read", "scan", "skim", "fetch"],
        ["what", "definition", "overview", "introduction"],
        ["problem", "limitation", "issue", "vs", "compare",
         "1.1", "predecessor", "improve"],
        ["innovation", "feature", "mechanism", "key", "main"],
        ["synthesi", "summari", "answer", "combine"],
    ],
    "expected_result_keywords": [
        ["http/2", "http2"],
        ["http/1", "1.1"],
        ["multiplex", "binary", "stream", "header compression",
         "hpack", "server push"],
        ["latency", "performance", "head-of-line", "blocking",
         "concurrent", "connection"],
    ],
    "expected_min_tool_calls": 3,
    "deadline_target_s": 90.0,
}

SCENARIO_3 = {
    "id": 3,
    "name": "Wikipedia: Bitcoin — quick read + honest take on risks",
    "url": "https://en.wikipedia.org/wiki/Bitcoin",
    "goal": (
        "Give me a quick read on what Bitcoin is and what its main risks "
        "or criticisms are. What's your honest take?"
    ),
    "expected_procedure_keywords": [
        ["read", "scan", "skim"],
        ["what", "definition", "overview"],
        ["risk", "criticism", "concern", "downside",
         "controversy", "problem"],
        ["opinion", "take", "synthesi", "judgment", "view"],
    ],
    "expected_result_keywords": [
        ["bitcoin"],
        ["decentral", "blockchain", "peer", "cryptocurrency",
         "satoshi", "mining"],
        ["energy", "electricity", "environment", "carbon",
         "volatility", "volatile", "regulation", "fraud",
         "ponzi", "speculation", "consumption"],
        # Hedged-opinion shape: the policy says "based on what's on the
        # page". Accept any honest framing.
        ["based on", "according to", "the page", "the article",
         "appears", "seems", "suggests", "my take", "honest"],
    ],
    "expected_min_tool_calls": 3,
    "deadline_target_s": 90.0,
}

ALL = [SCENARIO_1, SCENARIO_2, SCENARIO_3]
