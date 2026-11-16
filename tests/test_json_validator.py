import copy
import json
import subprocess
from pathlib import Path

from backend.tools.json_validator import validate_text

ROOT = Path(__file__).resolve().parents[1]
GOOD = {
    "url": "https://example.com/", "title": "Example Domain", "meta_description": "",
    "headings": [{"level": 1, "text": "Example Domain", "source": "https://example.com/"}],
    "paragraphs": [{"text": "This domain is for use in illustrative examples.", "source": "https://example.com/"}],
    "internal_links": [], "pages_crawled": 1, "sources": ["https://example.com/"],
}


def v(obj):
    return validate_text(json.dumps(obj))


def test_valid_document():
    r = v(GOOD)
    assert r["valid"] and r["errors"] == []


def test_syntax_error_reports_position():
    r = validate_text('{"url": "x",}')
    assert not r["valid"] and "line 1" in r["errors"][0]["message"]


def test_non_object_top_level():
    assert not validate_text("[1,2,3]")["valid"]


def test_missing_required_fields_listed():
    d = copy.deepcopy(GOOD); del d["title"]; del d["sources"]
    paths = {e["path"] for e in v(d)["errors"]}
    assert {"title", "sources"} <= paths


def test_wrong_types_are_rejected_strictly():
    d = copy.deepcopy(GOOD); d["pages_crawled"] = "1"
    assert not v(d)["valid"]
    d = copy.deepcopy(GOOD); d["headings"][0]["level"] = "1"
    assert not v(d)["valid"]
    d = copy.deepcopy(GOOD); d["paragraphs"] = "text"
    assert not v(d)["valid"]


def test_consistency_checks():
    d = copy.deepcopy(GOOD); d["pages_crawled"] = 2
    assert any("does not match" in e["message"] for e in v(d)["errors"])
    d = copy.deepcopy(GOOD); d["url"] = "ftp://x"
    assert not v(d)["valid"]
    d = copy.deepcopy(GOOD); d["pages_crawled"] = 0; d["sources"] = []
    assert not v(d)["valid"]


def test_cli_exit_codes_via_stdin():
    run = lambda payload: subprocess.run(["python3", "-B", str(ROOT / "backend/tools/json_validator.py"), "--stdin"],
                                         input=payload, capture_output=True, text=True, cwd=ROOT)
    ok, bad = run(json.dumps(GOOD)), run("{oops")
    assert ok.returncode == 0 and json.loads(ok.stdout)["valid"] is True
    assert bad.returncode == 1 and json.loads(bad.stdout)["valid"] is False
