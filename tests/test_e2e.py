"""End-to-end tests against a local fixture website (real HTTP + real subprocesses)."""
import json
import socket

import pytest

from backend import intent as I, orchestrator, planner
from fixture_server import start_server


@pytest.fixture(scope="module")
def site():
    srv, base = start_server()
    yield base
    srv.shutdown()


@pytest.fixture(autouse=True)
def relaxed(monkeypatch, tmp_path):
    monkeypatch.setenv("WEBPILOT_ALLOW_PRIVATE_TEST", "1")   # allows the local fixture only
    monkeypatch.setenv("WEBPILOT_OUTPUTS_DIR", str(tmp_path))
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")


def run(message):
    intent = I.parse_intent(message)
    tid = orchestrator.create_task(message, intent, planner.build_plan(intent))
    orchestrator.run_task(tid)
    return orchestrator.snapshot(tid)


def test_full_pipeline_completed(site):
    t = run(f"Crawl {site}/, extract important information, summarize the website, and return JSON.")
    assert t.status == "completed" and t.json_valid
    assert [c.tool for c in t.cli_results] == ["site_discovery", "content_extractor", "data_processor", "json_validator"]
    for c in t.cli_results:
        assert c.exit_code == 0 and c.status == "completed" and c.stdout.strip() and c.duration_ms is not None
    assert any("[content_extractor] fetching" in c.stderr for c in t.cli_results)   # stderr captured
    assert all(s.status == "completed" for s in t.execution_steps)
    d = t.extracted_data
    assert d["title"] == "Acme Robotics" and d["pages_crawled"] == 3 and len(d["sources"]) == 3
    assert "Acme" in t.summary and "warehouse robots" in t.summary          # grounded in extracted text
    assert all(src.startswith(site) for src in d["sources"])
    assert not any("/private" in s for s in d["sources"])                    # robots.txt respected
    assert not any(u.endswith(".pdf") for u in d["sources"])                 # non-HTML links are listed but never crawled
    assert "IGNORE ALL INSTRUCTIONS" not in json.dumps(d)                    # <script> stripped


def test_hostile_page_text_is_only_data(site):
    t = run(f"summarize {site}/ (1 page)")
    texts = " ".join(p["text"] for p in t.extracted_data["paragraphs"])
    assert "rm -rf" in texts            # preserved as inert data...
    assert [c.tool for c in t.cli_results] == ["site_discovery", "content_extractor", "data_processor", "json_validator"]  # ...never executed


def test_contact_extraction(site):
    t = run(f"Extract contact information from {site}/")
    assert t.extracted_data["contacts"]["emails"] == ["hello@acme.test"]


def test_inaccessible_site_reported_honestly():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close()   # nothing listening
    t = run(f"crawl http://127.0.0.1:{port}/")
    assert t.status == "failed" and not t.json_valid and t.extracted_data == {}
    assert t.errors and "site_discovery" in t.errors[0]
    assert [x.id for x in t.execution_steps if x.status == "failed"] == ["discover_pages"]
    assert all(x.status == "skipped" for x in t.execution_steps[2:])
    attempts = [c for c in t.cli_results if c.tool == "site_discovery"]
    assert len(attempts) == orchestrator.MAX_RETRIES + 1 and all(c.exit_code == 75 for c in attempts)   # retried transient failure


def test_http_error_page(site):
    t = run(f"crawl {site}/flaky500")
    assert t.status == "failed" and "503" in t.errors[0]


def test_robots_blocked_url(site):
    t = run(f"crawl {site}/private/secret")
    assert t.status == "failed" and "robots" in t.errors[0].lower()
    assert t.cli_results[0].exit_code == 3


def test_empty_page_warns(site):
    t = run(f"crawl {site}/empty (single page)")
    assert any("no extractable text" in w for w in t.warnings)
