"""Intent understanding: natural language -> validated Intent.

Groq (optional) proposes a structured intent; the result is validated against a
Pydantic schema and the URL must literally come from the user's message.
If Groq is unavailable or its output is invalid, deterministic rules are used.
"""
from __future__ import annotations

import json
import re
from urllib.parse import urlparse

from pydantic import ValidationError

from backend import ai
from backend.logger import get_logger
from backend.models import MAX_PAGES_LIMIT, AIIntentOutput, Intent

log = get_logger("webpilot.intent")

URL_RE = re.compile(r"https?://[^\s<>\"'`]+", re.I)
DOMAIN_RE = re.compile(r"(?<![@\w/.-])((?:[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.)+[a-z]{2,24})(/[^\s<>\"'`]*)?", re.I)
NOT_TLDS = {"json", "txt", "md", "py", "js", "html", "css", "csv", "pdf", "png", "jpg", "exe", "sh", "yml", "yaml"}
TRAILING = ".,;:!?)]}>\"'"


class NoUrlFound(ValueError):
    pass


def extract_urls(message: str) -> list[str]:
    urls = [u.rstrip(TRAILING) for u in URL_RE.findall(message)]
    if urls:
        return urls
    out = []
    for m in DOMAIN_RE.finditer(message):
        host, path = m.group(1), m.group(2) or ""
        if host.rsplit(".", 1)[-1].lower() in NOT_TLDS:
            continue
        out.append("https://" + host + path.rstrip(TRAILING))
    return out


def _key(url: str) -> str:
    p = urlparse(url)
    host = (p.hostname or "").lower()
    return host[4:] if host.startswith("www.") else host


def rule_based_intent(message: str, urls: list[str]) -> Intent:
    low = message.lower()
    notes = []
    if len(urls) > 1:
        notes.append(f"{len(urls)} URLs found; only the first ({urls[0]}) is crawled in this MVP.")
    if any(w in low for w in ("contact", "email", "e-mail", "phone")):
        task = "contact_info"
    elif "compar" in low:
        task = "compare_pages"
    elif ("heading" in low or "link" in low) and "summar" not in low:
        task = "headings_links"
    elif "json" in low and "summar" not in low and "extract" not in low:
        task = "structured_json"
    elif "extract" in low and "summar" not in low:
        task = "extract_info"
    else:
        task = "website_crawl"

    fields = ["title", "description", "headings", "paragraphs", "links"]
    if task == "headings_links":
        fields = ["headings", "links"]
    if task == "contact_info":
        fields.append("contacts")

    max_pages = MAX_PAGES_LIMIT
    m = re.search(r"(?:up to|max(?:imum)?(?: of)?|first|top)?\s*(\d+)\s*(?:pages?|urls?)", low)
    if m:
        max_pages = int(m.group(1))
    elif re.search(r"\b(single page|this page|homepage only|home page only|one page)\b", low):
        max_pages = 1
    if max_pages > MAX_PAGES_LIMIT:
        notes.append(f"Requested {max_pages} pages; capped at {MAX_PAGES_LIMIT} for the MVP.")

    wants_json = any(w in low for w in ("json", "structured", "extract", "data"))
    wants_summary = "summar" in low or "describe" in low or "overview" in low or not wants_json
    outputs = []
    if wants_summary:
        outputs.append("summary")
    if wants_json:
        outputs.append("JSON")
    return Intent(task_type=task, url=urls[0], fields=fields, summary_required=wants_summary,
                  json_required=wants_json or task in ("structured_json", "extract_info"),
                  max_pages=max_pages, expected_output=" + ".join(outputs) or "summary",
                  source="rules", notes=notes)


SYSTEM_PROMPT = """You convert a user's request about crawling a website into a JSON object.
Return ONLY JSON with these keys:
task_type: one of website_crawl, extract_info, headings_links, structured_json, contact_info, compare_pages
url: the website URL exactly as written by the user (add https:// if missing)
fields: array from [title, description, headings, paragraphs, links, contacts]
summary_required: boolean
json_required: boolean
max_pages: integer 1-3
expected_output: short string
steps: optional array chosen from [validate_url, discover_pages, extract_content, process_data, validate_json, generate_summary]
Never include shell commands, other keys or explanations."""


def _loads_lenient(raw: str):
    """Parse model output as JSON, tolerating markdown fences or surrounding prose."""
    raw = (raw or "").strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw, flags=re.I).strip()
    try:
        return json.loads(raw)
    except ValueError:
        m = re.search(r"\{.*\}", raw, re.S)
        if not m:
            raise
        return json.loads(m.group(0))


def ai_intent(message: str, urls: list[str]) -> Intent | None:
    if not ai.groq_configured():
        return None
    try:
        raw = ai.groq_chat([{"role": "system", "content": SYSTEM_PROMPT},
                            {"role": "user", "content": message}], json_mode=True)
        parsed = AIIntentOutput.model_validate(_loads_lenient(raw))
    except ValidationError as exc:
        first = exc.errors()[0]
        log.warning("Groq intent rejected, using rules: %s -> %s",
                    ".".join(str(x) for x in first["loc"]) or "output", first["msg"])
        return None
    except (RuntimeError, ValueError) as exc:
        log.warning("Groq intent unavailable, using rules: %s", exc)
        return None
    ai_url = parsed.url if re.match(r"https?://", parsed.url, re.I) else "https://" + parsed.url
    if _key(ai_url) not in {_key(u) for u in urls}:
        log.warning("Groq returned URL %r which is not in the user's message; ignoring AI output", parsed.url)
        return None
    # keep the user's literal URL (with its path) rather than the model's rewrite
    url = next(u for u in urls if _key(u) == _key(ai_url))
    base = rule_based_intent(message, urls)
    return Intent(task_type=parsed.task_type, url=url, fields=parsed.fields or base.fields,
                  summary_required=parsed.summary_required, json_required=parsed.json_required,
                  max_pages=parsed.max_pages, expected_output=parsed.expected_output or base.expected_output,
                  source="groq", notes=base.notes)


def parse_intent(message: str) -> Intent:
    urls = extract_urls(message)
    if not urls:
        raise NoUrlFound("I couldn't find a website URL in your message. Include one, e.g. https://example.com")
    return ai_intent(message, urls) or rule_based_intent(message, urls)
