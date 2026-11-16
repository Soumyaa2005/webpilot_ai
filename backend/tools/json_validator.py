#!/usr/bin/env python3
"""CLI tool 4: validate JSON syntax, required fields and types.

Usage: python backend/tools/json_validator.py --input outputs/demo/processed.json
Exit code 0 = valid, 1 = invalid.
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from pydantic import ValidationError  # noqa: E402

from backend.models import ExtractedData  # noqa: E402
from backend.utils.helpers import EXIT_FAIL, EXIT_OK, EXIT_USAGE, emit, log, safe_path, write_json  # noqa: E402

REQUIRED = ["url", "title", "meta_description", "headings", "paragraphs",
            "internal_links", "pages_crawled", "sources"]


def validate_text(text: str) -> dict:
    errors: list[dict] = []
    try:
        obj = json.loads(text)
    except json.JSONDecodeError as exc:
        return {"valid": False, "errors": [{"path": "$", "message":
                f"Invalid JSON syntax at line {exc.lineno}, column {exc.colno}: {exc.msg}"}]}
    if not isinstance(obj, dict):
        return {"valid": False, "errors": [{"path": "$", "message": "Top-level JSON value must be an object"}]}
    for key in REQUIRED:
        if key not in obj:
            errors.append({"path": key, "message": "Required field is missing"})
    if not errors:
        try:
            ExtractedData.model_validate(obj)
        except ValidationError as exc:
            for e in exc.errors():
                path = ".".join(str(p) for p in e["loc"]) or "$"
                errors.append({"path": path, "message": e["msg"]})
        if not errors:
            if not obj["url"].startswith(("http://", "https://")):
                errors.append({"path": "url", "message": "must start with http:// or https://"})
            if obj["pages_crawled"] != len(obj["sources"]):
                errors.append({"path": "pages_crawled", "message":
                               f"pages_crawled ({obj['pages_crawled']}) does not match number of sources ({len(obj['sources'])})"})
            if obj["pages_crawled"] == 0:
                errors.append({"path": "pages_crawled", "message": "no pages were crawled"})
    return {"valid": not errors, "errors": errors, "checked_fields": REQUIRED}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Validate extracted-data JSON")
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--input", help="path to JSON file (inside outputs/)")
    src.add_argument("--stdin", action="store_true", help="read JSON from stdin")
    ap.add_argument("--output", help="write validation report here (inside outputs/)")
    args = ap.parse_args(argv)
    try:
        text = sys.stdin.read() if args.stdin else safe_path(args.input).read_text(encoding="utf-8")
        out = safe_path(args.output) if args.output else None
    except (OSError, ValueError, UnicodeDecodeError) as exc:
        emit({"tool": "json_validator", "status": "error", "valid": False, "error": str(exc)})
        return EXIT_USAGE
    report = validate_text(text)
    report["tool"] = "json_validator"
    report["status"] = "ok" if report["valid"] else "invalid"
    if out:
        write_json(out, report)
    emit(report)
    for e in report["errors"]:
        log(f"[json_validator] {e['path']}: {e['message']}")
    return EXIT_OK if report["valid"] else EXIT_FAIL


if __name__ == "__main__":
    sys.exit(main())
