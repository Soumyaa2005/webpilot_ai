"""Failure, partial-success, timeout, AI-path and secret-isolation behaviour."""
import dataclasses
import json
import logging

import pytest

from backend import ai, intent as I, logger, orchestrator, planner
from backend.tools import registry
from fixture_server import start_server


@pytest.fixture(scope="module")
def site():
    srv, base = start_server()
    yield base
    srv.shutdown()


@pytest.fixture(autouse=True)
def relaxed(monkeypatch, tmp_path):
    monkeypatch.setenv("WEBPILOT_ALLOW_PRIVATE_TEST", "1")
    monkeypatch.setenv("WEBPILOT_OUTPUTS_DIR", str(tmp_path))
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")


def run(message):
    intent = I.parse_intent(message)
    tid = orchestrator.create_task(message, intent, planner.build_plan(intent))
    orchestrator.run_task(tid)
    return orchestrator.snapshot(tid)


def test_partial_success_when_one_page_fails(site):
    t = run(f"crawl {site}/partial")
    assert t.status == "partial_success" and t.json_valid          # data is valid but incomplete
    assert t.extracted_data["pages_crawled"] == 2
    assert any("flaky500" in w and "503" in w for w in t.warnings)
    assert t.extracted_data["crawl_errors"]                         # failure preserved in the JSON
    assert all(s.status == "completed" for s in t.execution_steps)


def test_tool_timeout_is_killed_reported_and_retried(site, monkeypatch):
    spec = registry.REGISTRY["site_discovery"]
    monkeypatch.setitem(registry.REGISTRY, "site_discovery", dataclasses.replace(spec, timeout=2))
    monkeypatch.setattr(orchestrator, "MAX_RETRIES", 1)
    t = run(f"crawl {site}/slow")
    runs = [c for c in t.cli_results if c.tool == "site_discovery"]
    assert t.status == "failed" and "timed out" in t.errors[0]
    assert len(runs) == 2 and all(c.status == "timeout" and c.exit_code is None for c in runs)
    assert "killed after 2s timeout" in runs[0].stderr


def test_processing_failure_preserves_partial_results(site, monkeypatch):
    spec = registry.REGISTRY["data_processor"]
    monkeypatch.setitem(registry.REGISTRY, "data_processor", dataclasses.replace(spec, script="missing_tool.py"))
    t = run(f"crawl {site}/")
    assert t.status == "partial_success" and not t.json_valid
    assert t.extracted_data["partial"] is True and t.extracted_data["raw_extraction"]["pages"]
    states = {s.id: s.status for s in t.execution_steps}
    assert states["extract_content"] == "completed" and states["process_data"] == "failed"
    assert states["validate_json"] == "skipped" and states["generate_summary"] == "skipped"


def test_invalid_json_is_never_reported_as_completed(site, monkeypatch):
    spec = registry.REGISTRY["json_validator"]
    monkeypatch.setitem(registry.REGISTRY, "json_validator", dataclasses.replace(spec, script="missing_tool.py"))
    t = run(f"crawl {site}/")
    assert t.status == "partial_success" and t.json_valid is False


def test_groq_path_with_mocked_api(site, monkeypatch):
    monkeypatch.setattr(ai, "groq_configured", lambda: True)
    def fake(messages, json_mode=False, max_tokens=500):
        if json_mode:
            return json.dumps({"task_type": "website_crawl", "url": f"{site}/", "fields": ["title"], "max_pages": 1})
        assert "untrusted_data" in messages[0]["content"] and "<untrusted_data>" in messages[1]["content"]
        return "A demo site about warehouse robots."
    monkeypatch.setattr(ai, "groq_chat", fake)
    t = run(f"crawl {site}/")
    assert t.intent["source"] == "groq" and t.status == "completed"
    assert t.ai_interpretation["text"].startswith("A demo site")
    assert "not extracted" in t.ai_interpretation["label"]
    assert "A demo site" not in t.summary                      # AI text never mixed into the extracted summary
    assert "A demo site" not in json.dumps(t.extracted_data)


def test_ai_failure_does_not_break_task(site, monkeypatch):
    monkeypatch.setattr(ai, "groq_configured", lambda: True)
    def boom(*a, **k): raise RuntimeError("Groq returned HTTP 500")
    monkeypatch.setattr(ai, "groq_chat", boom)
    t = run(f"crawl {site}/")
    assert t.status == "completed" and t.ai_interpretation is None


def test_secrets_not_passed_to_tools_or_logs(monkeypatch, caplog):
    monkeypatch.setenv("GROQ_API_KEY", "gsk_supersecretvalue12345")
    env = orchestrator._tool_env()
    assert "GROQ_API_KEY" not in env and "gsk_supersecretvalue12345" not in json.dumps(env)
    lg = logger.get_logger("webpilot.test")
    seen = []
    class H(logging.Handler):
        def emit(self, r): seen.append(r.getMessage())
    h = H(); h.addFilter(logger.RedactingFilter()); lg.addHandler(h)
    lg.warning("key is gsk_supersecretvalue12345 end")
    assert "gsk_supersecretvalue12345" not in seen[0] and "REDACTED" in seen[0]


def test_report_persists_to_disk(site, tmp_path):
    t = run(f"crawl {site}/")
    assert (tmp_path / t.task_id / "report.json").exists()
    orchestrator._TASKS.pop(t.task_id)                           # simulate server restart
    again = orchestrator.snapshot(t.task_id)
    assert again and again.status == "completed" and again.extracted_data["title"] == "Acme Robotics"
