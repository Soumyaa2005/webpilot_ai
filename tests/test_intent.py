import json

import pytest

from backend import ai, intent as I, planner


def test_mandatory_prompt_rules():
    it = I.parse_intent("Crawl https://example.com, extract important information, summarize the website, and return the result in JSON.")
    assert it.url == "https://example.com"          # trailing comma stripped
    assert it.summary_required and it.json_required
    assert it.max_pages == 3 and it.source == "rules"
    plan = planner.build_plan(it)
    assert plan.steps == ["validate_url", "discover_pages", "extract_content", "process_data", "validate_json", "generate_summary"]
    assert plan.output_format == ["summary", "json"]


def test_bare_domain_gets_https():
    assert I.parse_intent("summarize example.org/about please").url == "https://example.org/about"


def test_filenames_are_not_domains():
    with pytest.raises(I.NoUrlFound):
        I.parse_intent("please read config.json and notes.txt")


def test_no_url_raises():
    with pytest.raises(I.NoUrlFound):
        I.parse_intent("Summarize the internet for me")


def test_task_types_and_fields():
    assert I.parse_intent("Find the main headings and links on https://a.com").task_type == "headings_links"
    c = I.parse_intent("Extract contact information from https://a.com")
    assert c.task_type == "contact_info" and "contacts" in c.fields
    assert I.parse_intent("Compare https://a.com pages").task_type == "compare_pages"


def test_page_limits_are_clamped():
    it = I.parse_intent("crawl 50 pages of https://a.com")
    assert it.max_pages == 3 and any("capped" in n for n in it.notes)
    assert I.parse_intent("only this page https://a.com").max_pages == 1


def test_multiple_urls_note():
    it = I.parse_intent("compare https://a.com and https://b.com")
    assert it.url == "https://a.com" and it.notes


@pytest.fixture
def groq(monkeypatch):
    monkeypatch.setattr(ai, "groq_configured", lambda: True)
    def setter(payload):
        monkeypatch.setattr(ai, "groq_chat", lambda *a, **k: payload if isinstance(payload, str) else json.dumps(payload))
    return setter


def test_valid_groq_output_is_used(groq):
    groq({"task_type": "website_crawl", "url": "https://example.com", "fields": ["title"], "max_pages": 2,
          "steps": ["validate_url", "discover_pages"]})
    it = I.parse_intent("crawl https://example.com")
    assert it.source == "groq" and it.max_pages == 2


@pytest.mark.parametrize("payload", [
    {"task_type": "website_crawl", "url": "https://evil.test", "fields": []},                       # URL not from user
    {"task_type": "run_shell", "url": "https://example.com"},                                      # unknown task
    {"task_type": "website_crawl", "url": "https://example.com", "steps": ["bash"]},               # unapproved step
    {"task_type": "website_crawl", "url": "https://example.com", "steps": ["extract_content", "validate_url"]},
    "not json at all",
])
def test_bad_groq_output_falls_back_to_rules(groq, payload):
    groq(payload)
    it = I.parse_intent("crawl https://example.com")
    assert it.source == "rules" and it.url == "https://example.com"


def test_groq_failure_falls_back(monkeypatch):
    monkeypatch.setattr(ai, "groq_configured", lambda: True)
    def boom(*a, **k): raise RuntimeError("down")
    monkeypatch.setattr(ai, "groq_chat", boom)
    assert I.parse_intent("crawl https://example.com").source == "rules"


def test_extra_keys_and_unknown_fields_are_ignored_not_executed(groq):
    groq({"task_type": "website_crawl", "url": "https://example.com", "command": "rm -rf /",
          "fields": ["title", "meta_description", "headings"], "max_pages": 9, "output_format": "json"})
    it = I.parse_intent("crawl https://example.com")
    assert it.source == "groq" and it.fields == ["title", "headings"] and it.max_pages == 3
    assert not hasattr(it, "command")


def test_rejection_reason_is_logged(groq, caplog):
    groq({"task_type": "run_shell", "url": "https://example.com"})
    with caplog.at_level("WARNING", logger="webpilot.intent"):
        I.log.propagate = True
        I.parse_intent("crawl https://example.com")
        I.log.propagate = False
    assert any("task_type" in r.getMessage() for r in caplog.records)


@pytest.mark.parametrize("wrap", ["```json\n{}\n```", "Here you go: {}", "{}"])
def test_fenced_or_chatty_json_is_accepted(groq, wrap):
    body = json.dumps({"task_type": "website_crawl", "url": "https://example.com", "fields": ["title"]})
    groq(wrap.replace("{}", body))
    assert I.parse_intent("crawl https://example.com").source == "groq"
