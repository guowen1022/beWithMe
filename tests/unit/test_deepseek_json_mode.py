"""Regression guard: deepseek `generate_json` must disable thinking.

deepseek-v4-flash is a thinking-enabled model; its hidden chain-of-thought
counts against `max_tokens`. Under JSON mode with a tight budget, the CoT
consumed the whole budget and `message.content` came back EMPTY — which
silently broke every `generate_json` caller (Maestro candidate generation
always returned 0 candidates -> the long instance could never ACT; the
persona router was at risk too). The fix passes
`extra_body={"thinking": {"type": "disabled"}}`. This test pins that so a
future "cleanup" of extra_body can't quietly reintroduce the outage.

Pure unit test — stubs the client, no network.
"""
import asyncio
import types

import infra.model.deepseek.llm as ds


class _FakeMessage:
    content = '[{"title": "x"}]'


class _FakeChoice:
    message = _FakeMessage()
    finish_reason = "stop"


class _FakeResponse:
    choices = [_FakeChoice()]
    usage = None


class _RecordingCompletions:
    def __init__(self):
        self.kwargs = None

    async def create(self, **kwargs):
        self.kwargs = kwargs
        return _FakeResponse()


class _FakeClient:
    def __init__(self):
        self.chat = types.SimpleNamespace(completions = _RecordingCompletions())


def test_generate_json_disables_thinking(monkeypatch):
    fake = _FakeClient()
    monkeypatch.setattr(ds, "_get_client", lambda: fake)

    out = asyncio.run(ds.generate_json("give me json", max_tokens=256))

    kwargs = fake.chat.completions.kwargs
    assert kwargs is not None, "create() was never called"
    # The fix: thinking must be explicitly disabled so the budget is spent
    # on JSON content, not hidden reasoning.
    assert kwargs.get("extra_body") == {"thinking": {"type": "disabled"}}
    # JSON mode is still requested.
    assert kwargs.get("response_format") == {"type": "json_object"}
    # The caller's content comes back intact.
    assert out == '[{"title": "x"}]'
