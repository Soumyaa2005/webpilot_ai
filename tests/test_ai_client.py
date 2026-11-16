import pytest
from backend import ai


class Resp:
    def __init__(self, status=200, body=None):
        self.status_code, self._body = status, body if body is not None else {}
    def json(self):
        return self._body


@pytest.fixture
def capture(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "gsk_testkey1234567890")
    sent = {}
    def fake_post(url, json=None, headers=None, timeout=None):
        sent.update(json); return sent.get("_resp") or Resp(200, {"choices": [{"message": {"content": "{\"ok\": true}"}}]})
    monkeypatch.setattr(ai.requests, "post", fake_post)
    return sent


def test_gpt_oss_gets_low_reasoning_and_token_headroom(monkeypatch, capture):
    monkeypatch.setenv("GROQ_MODEL", "openai/gpt-oss-20b")
    assert ai.groq_chat([{"role": "user", "content": "x"}], json_mode=True, max_tokens=250) == '{"ok": true}'
    assert capture["reasoning_effort"] == "low" and capture["max_tokens"] >= 1500
    assert capture["response_format"] == {"type": "json_object"}


def test_other_models_do_not_get_reasoning_param(monkeypatch, capture):
    monkeypatch.setenv("GROQ_MODEL", "llama-3.1-8b-instant")
    ai.groq_chat([{"role": "user", "content": "x"}])
    assert "reasoning_effort" not in capture


def test_empty_message_and_http_error_detail(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "gsk_testkey1234567890")
    monkeypatch.setattr(ai.requests, "post", lambda *a, **k: Resp(200, {"choices": [{"message": {"content": ""}}]}))
    with pytest.raises(RuntimeError, match="empty message"):
        ai.groq_chat([{"role": "user", "content": "x"}])
    monkeypatch.setattr(ai.requests, "post", lambda *a, **k: Resp(404, {"error": {"message": "model does not exist"}}))
    with pytest.raises(RuntimeError, match="HTTP 404 .*model does not exist"):
        ai.groq_chat([{"role": "user", "content": "x"}])


def test_json_mode_400_retries_without_response_format(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "gsk_testkey1234567890")
    monkeypatch.setenv("GROQ_MODEL", "openai/gpt-oss-20b")
    calls = []
    def fake_post(url, json=None, headers=None, timeout=None):
        calls.append(json)
        if "response_format" in json:
            return Resp(400, {"error": {"message": "Tool choice is none, but model called a tool"}})
        return Resp(200, {"choices": [{"message": {"content": "{\"ok\": true}"}}]})
    monkeypatch.setattr(ai.requests, "post", fake_post)
    assert ai.groq_chat([{"role": "user", "content": "x"}], json_mode=True) == '{"ok": true}'
    assert len(calls) == 2 and "response_format" not in calls[1] and "JSON object only" in calls[1]["messages"][-1]["content"]
