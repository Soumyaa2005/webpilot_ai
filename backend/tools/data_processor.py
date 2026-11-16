#!/usr/bin/env python3
"""CLI tool 3: clean, de-duplicate and organise extracted content; build a
factual extractive summary from the REAL extracted text (no model involved).

Usage: python backend/tools/data_processor.py --input outputs/demo/extract.json \
           --output outputs/demo/processed.json [--fields contacts]
"""
import argparse
import re
import sys
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from backend.utils.helpers import (EXIT_FAIL, EXIT_OK, EXIT_USAGE, emit, log,  # noqa: E402
                                   normalize_ws, read_json, safe_path, truncate, utc_now,
                                   write_json)

MAX_HEADINGS, MAX_PARAGRAPHS, MAX_LINKS = 60, 40, 60


def first_sentence(text: str, limit: int = 220) -> str:
    parts = re.split(r"(?<=[.!?])\s+", text.strip(), maxsplit=1)
    return truncate(parts[0], limit)


def build_summary(data: dict) -> tuple[str, str]:
    """Return (summary, basis). Every sentence is assembled from extracted strings."""
    host = urlparse(data["url"]).hostname or data["url"]
    title = data["title"] or host
    bits = [f"{title} ({host})"]
    if data["meta_description"]:
        bits[0] += f": {truncate(data['meta_description'], 260)}"
    else:
        bits[0] += "."
    parts = [bits[0] if bits[0].endswith((".", "!", "?")) else bits[0] + "."]

    heads = []
    for h in sorted(data["headings"], key=lambda x: x["level"]):
        if h["text"].lower() != title.lower() and h["text"] not in heads:
            heads.append(h["text"])
        if len(heads) >= 5:
            break
    if heads:
        parts.append("Main sections: " + "; ".join(heads) + ".")

    used = data["meta_description"].lower()
    picked = []
    for p in data["paragraphs"]:
        s = first_sentence(p["text"])
        if s.lower() not in used and s not in picked:
            picked.append(s)
        if len(picked) >= 2:
            break
    if picked:
        parts.append("Key content: " + " ".join(s if s.endswith((".", "!", "?")) else s + "." for s in picked))
    n = data["pages_crawled"]
    parts.append(f"Based on {n} crawled page{'s' if n != 1 else ''}.")
    basis = "Extractive: assembled from the page title, meta description, headings and paragraphs found in the crawl."
    return " ".join(parts), basis


def process(raw: dict, want_contacts: bool) -> dict:
    pages = raw.get("pages", [])
    if not pages:
        raise ValueError("no pages in extraction output")
    headings, paragraphs, links, sources, page_overview = [], [], [], [], []
    seen_h, seen_p, seen_l = set(), set(), set()
    emails, phones = set(), set()
    for pg in pages:
        src = pg.get("final_url") or pg["url"]
        sources.append(src)
        for h in pg.get("headings", []):
            text = normalize_ws(h.get("text", ""))
            if text and text.lower() not in seen_h and len(headings) < MAX_HEADINGS:
                seen_h.add(text.lower())
                headings.append({"level": int(h.get("level", 2)), "text": text, "source": src})
        for p in pg.get("paragraphs", []):
            text = normalize_ws(p)
            key = text.lower()[:80]
            if len(text) >= 20 and key not in seen_p and len(paragraphs) < MAX_PARAGRAPHS:
                seen_p.add(key)
                paragraphs.append({"text": text, "source": src})
        for l in pg.get("internal_links", []):
            if l["url"] not in seen_l and len(links) < MAX_LINKS:
                seen_l.add(l["url"])
                links.append({"text": normalize_ws(l.get("text", "")), "url": l["url"], "source": src})
        emails.update(pg.get("emails", []))
        phones.update(pg.get("phones", []))
        page_overview.append({
            "url": src, "title": normalize_ws(pg.get("title", "")),
            "meta_description": normalize_ws(pg.get("meta_description", "")),
            "heading_count": len(pg.get("headings", [])),
            "paragraph_count": len(pg.get("paragraphs", [])),
            "word_count": pg.get("word_count", 0), "empty": bool(pg.get("empty")),
        })
    primary = pages[0]
    data = {
        "url": primary.get("final_url") or primary["url"],
        "title": next((normalize_ws(p.get("title", "")) for p in pages if p.get("title")), ""),
        "meta_description": next((normalize_ws(p.get("meta_description", "")) for p in pages
                                  if p.get("meta_description")), ""),
        "headings": headings,
        "paragraphs": paragraphs,
        "internal_links": links,
        "pages_crawled": len(pages),
        "sources": sources,
        "pages": page_overview,
        "crawl_errors": raw.get("errors", []),
        "processed_at": utc_now(),
    }
    if want_contacts:
        data["contacts"] = {"emails": sorted(emails), "phones": sorted(phones),
                            "note": "Only publicly listed mailto:/tel: links and plain-text emails."}
    data["summary"], data["summary_basis"] = build_summary(data)
    return data


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Clean and organise extracted content")
    ap.add_argument("--input", required=True, help="JSON produced by content_extractor")
    ap.add_argument("--output", help="write processed JSON here (inside outputs/)")
    ap.add_argument("--fields", default="", help="comma list, e.g. contacts")
    args = ap.parse_args(argv)
    try:
        raw = read_json(safe_path(args.input))
        out = safe_path(args.output) if args.output else None
        data = process(raw, "contacts" in args.fields.split(","))
    except (ValueError, KeyError, OSError) as exc:
        log(f"[data_processor] failed: {exc}")
        emit({"tool": "data_processor", "status": "error", "error": str(exc)})
        return EXIT_USAGE if isinstance(exc, (OSError,)) else EXIT_FAIL
    if out:
        write_json(out, data)
        emit({"tool": "data_processor", "status": "ok", "pages_crawled": data["pages_crawled"],
              "headings": len(data["headings"]), "paragraphs": len(data["paragraphs"]),
              "links": len(data["internal_links"]), "output_file": str(out)})
    else:
        emit({"tool": "data_processor", "status": "ok", **data})
    log(f"[data_processor] processed {data['pages_crawled']} page(s)")
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
