"""Unit tests for the EOU endpoint in services/transcribe/main.py.

These tests do NOT load the real ONNX model — they stub `_get_eou` and
`_eou_infer_sync` so we can pin the contract:

  * `/api/eou` returns 503 when the model paths are unset.
  * Posting empty transcripts returns end_of_turn=False without inference.
  * Threshold override works and end_of_turn flips at the boundary.
  * `_format_eou_input` produces the chat-template shape we expect — a
    golden-input test that catches accidental template drift (which the
    plan flagged as silently catastrophic).
"""
from __future__ import annotations

from fastapi.testclient import TestClient


def _make_app():
    from services.transcribe.main import app
    return TestClient(app)


def test_eou_disabled_returns_503(monkeypatch):
    from services.transcribe import main as t

    # Make sure we don't accidentally hit a real model — also test the
    # canonical "feature off" path.
    monkeypatch.setattr(t.settings, "eou_model_path", "")
    monkeypatch.setattr(t.settings, "eou_tokenizer_path", "")
    # Reset any cached session so the disabled check kicks in.
    monkeypatch.setattr(t, "_eou_session", None)
    monkeypatch.setattr(t, "_eou_tokenizer", None)

    client = _make_app()
    res = client.post("/api/eou", json={"transcripts": ["hello"]})
    assert res.status_code == 503


def test_eou_empty_transcripts_short_circuits(monkeypatch):
    """No text to score → return end_of_turn=False without invoking the model."""
    from services.transcribe import main as t

    # Pretend the model is loaded so _get_eou succeeds without onnxruntime.
    monkeypatch.setattr(t.settings, "eou_model_path", "/fake/model.onnx")
    monkeypatch.setattr(t.settings, "eou_tokenizer_path", "/fake/tok")
    monkeypatch.setattr(t, "_eou_session", object())
    monkeypatch.setattr(t, "_eou_tokenizer", object())

    # If inference fires for empty input, we'll see this raise.
    def _explode(*_args, **_kw):
        raise AssertionError("inference must NOT run on empty transcripts")
    monkeypatch.setattr(t, "_eou_infer_sync", _explode)

    client = _make_app()
    res = client.post("/api/eou", json={"transcripts": []})
    assert res.status_code == 200
    body = res.json()
    assert body["end_of_turn"] is False
    assert body["end_prob"] == 0.0


def test_eou_threshold_boundary(monkeypatch):
    """end_of_turn flips precisely at the threshold."""
    from services.transcribe import main as t

    monkeypatch.setattr(t.settings, "eou_model_path", "/fake/model.onnx")
    monkeypatch.setattr(t.settings, "eou_tokenizer_path", "/fake/tok")
    monkeypatch.setattr(t.settings, "eou_threshold", 0.5)
    monkeypatch.setattr(t, "_eou_session", object())
    monkeypatch.setattr(t, "_eou_tokenizer", object())

    # Return whatever the test asks for via a side-channel.
    box = {"prob": 0.0}
    monkeypatch.setattr(t, "_eou_infer_sync", lambda *a, **k: box["prob"])

    client = _make_app()

    # Below threshold → not done.
    box["prob"] = 0.49
    res = client.post("/api/eou", json={"transcripts": ["what is the"]})
    assert res.status_code == 200
    assert res.json()["end_of_turn"] is False

    # At threshold → done.
    box["prob"] = 0.5
    res = client.post("/api/eou", json={"transcripts": ["what is the"]})
    assert res.status_code == 200
    assert res.json()["end_of_turn"] is True

    # Override via request body.
    box["prob"] = 0.51
    res = client.post(
        "/api/eou",
        json={"transcripts": ["what is the"], "threshold": 0.99},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["threshold"] == 0.99
    assert body["end_of_turn"] is False


def test_eou_format_input_golden_template():
    """Pin the exact chat template. If a refactor changes newlines or role
    tokens, this test fails LOUDLY — the model is silently catastrophic
    when the input shape drifts."""
    from services.transcribe.main import _format_eou_input

    # Single-turn (no prior history).
    out = _format_eou_input([], "what is the difference between")
    assert out == "<|im_start|>user\nwhat is the difference between"

    # With prior conversation.
    out = _format_eou_input(
        [
            {"role": "user", "text": "tell me about cats"},
            {"role": "assistant", "text": "they purr."},
        ],
        "and what about",
    )
    assert out == (
        "<|im_start|>user\ntell me about cats<|im_end|>\n"
        "<|im_start|>assistant\nthey purr.<|im_end|>\n"
        "<|im_start|>user\nand what about"
    )

    # Empty/whitespace prior turns are skipped (otherwise we'd inject
    # blank role blocks the model has never seen at train time).
    out = _format_eou_input(
        [{"role": "user", "text": "   "}, {"role": "assistant", "text": "hi"}],
        "ok",
    )
    assert out == (
        "<|im_start|>assistant\nhi<|im_end|>\n"
        "<|im_start|>user\nok"
    )
