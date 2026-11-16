#!/usr/bin/env python3
"""CLI tool 2: fetch pages and extract structured content.

Usage: python backend/tools/content_extractor.py --discovery-file outputs/demo/discovery.json \
           --max-pages 3 --output outputs/demo/extract.json
   or: python backend/tools/content_extractor.py --url https://example.com --output ...
"""
import argparse
import re
import sys
import time
from pathlib import Path
from urllib.parse import urldefrag, urljoin, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from bs4 import BeautifulSoup  # noqa: E402

from backend.security import (FetchError, RobotsChecker, safe_get, validate_url)  # noqa: E402
from backend.utils.helpers import (EXIT_FAIL, EXIT_OK, EXIT_ROBOTS, EXIT_TRANSIENT,  # noqa: E402
                                   EXIT_USAGE, emit, log, normalize_ws, read_json, safe_path,
                                   truncate, utc_now, write_json)

NOISE_TAGS = ["script", "style", "noscript", "svg", "iframe", "canvas", "template",
              "nav", "footer", "aside", "form", "button", "select", "dialog"]
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
MIN_PARAGRAPH = 40
MAX_PARAGRAPH = 700


def host_key(url: str) -> str:
    h = (urlparse(url).hostname or "").lower()
    return h[4:] if h.startswith("www.") else h


def extract_page(content: bytes, final_url: str) -> dict:
    soup = BeautifulSoup(content, "html.parser")
    base = host_key(final_url)

    # Collect links, emails and phones BEFORE stripping navigation noise.
    links, seen_links, emails, phones = [], set(), set(), set()
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if href.lower().startswith("mailto:"):
            emails.add(href[7:].split("?")[0].strip().lower())
            continue
        if href.lower().startswith("tel:"):
            phones.add(normalize_ws(href[4:]))
            continue
        if not href or href.startswith(("#", "javascript:", "data:")):
            continue
        absu = urldefrag(urljoin(final_url, href))[0]
        if urlparse(absu).scheme not in ("http", "https") or host_key(absu) != base:
            continue
        if absu in seen_links:
            continue
        seen_links.add(absu)
        links.append({"text": truncate(normalize_ws(a.get_text(" ")), 120), "url": absu})
        if len(links) >= 40:
            break

    title = ""
    if soup.title and soup.title.string:
        title = normalize_ws(soup.title.string)
    if not title:
        og = soup.find("meta", attrs={"property": "og:title"})
        title = normalize_ws(og.get("content", "")) if og else ""
    meta = soup.find("meta", attrs={"name": re.compile("^description$", re.I)}) or \
        soup.find("meta", attrs={"property": "og:description"})
    description = normalize_ws(meta.get("content", "")) if meta else ""

    for tag in soup(NOISE_TAGS):
        tag.decompose()
    for tag in soup.find_all(attrs={"role": re.compile("^(navigation|banner|search)$", re.I)}):
        tag.decompose()

    headings, seen_h = [], set()
    for h in soup.find_all(["h1", "h2", "h3"]):
        text = truncate(normalize_ws(h.get_text(" ")), 200)
        if text and text.lower() not in seen_h:
            seen_h.add(text.lower())
            headings.append({"level": int(h.name[1]), "text": text})
    if not title and headings:
        title = headings[0]["text"]

    paragraphs, seen_p = [], set()
    for p in soup.find_all("p"):
        text = normalize_ws(p.get_text(" "))
        if len(text) >= MIN_PARAGRAPH and text.lower() not in seen_p:
            seen_p.add(text.lower())
            paragraphs.append(truncate(text, MAX_PARAGRAPH))
        if len(paragraphs) >= 30:
            break
    if not paragraphs:  # fallback for sites that don't use <p>
        for el in soup.find_all(["li", "div", "td", "span"]):
            if el.find(["p", "div", "li"]):
                continue
            text = normalize_ws(el.get_text(" "))
            if len(text) >= 60 and text.lower() not in seen_p:
                seen_p.add(text.lower())
                paragraphs.append(truncate(text, MAX_PARAGRAPH))
            if len(paragraphs) >= 15:
                break

    body_text = normalize_ws(soup.get_text(" "))
    emails.update(e.lower() for e in EMAIL_RE.findall(body_text))
    return {
        "title": title,
        "meta_description": description,
        "headings": headings[:40],
        "paragraphs": paragraphs,
        "internal_links": links,
        "emails": sorted(emails)[:20],
        "phones": sorted(phones)[:10],
        "word_count": len(body_text.split()),
        "empty": not (title or headings or paragraphs),
    }


def select_urls(args) -> list[str]:
    urls = []
    if args.discovery_file:
        d = read_json(safe_path(args.discovery_file))
        urls.append(d.get("final_url") or d["start_url"])
        urls.extend(item["url"] for item in d.get("discovered", []))
    for u in args.url or []:
        urls.append(u)
    out, seen = [], set()
    for u in urls:
        key = urldefrag(u)[0].rstrip("/")
        if key not in seen:
            seen.add(key)
            out.append(u)
    return out[: args.max_pages]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Extract structured content from web pages")
    ap.add_argument("--url", action="append", help="page URL (repeatable)")
    ap.add_argument("--discovery-file", help="JSON produced by site_discovery")
    ap.add_argument("--max-pages", type=int, default=3)
    ap.add_argument("--output", help="write full JSON result to this file (inside outputs/)")
    args = ap.parse_args(argv)
    args.max_pages = max(1, min(args.max_pages, 3))
    if not args.url and not args.discovery_file:
        emit({"tool": "content_extractor", "status": "error", "error": "provide --url or --discovery-file"})
        return EXIT_USAGE
    try:
        out = safe_path(args.output) if args.output else None
        urls = select_urls(args)
    except (ValueError, KeyError, OSError) as exc:
        emit({"tool": "content_extractor", "status": "error", "error": f"bad input: {exc}"})
        return EXIT_USAGE

    robots, pages, errors = RobotsChecker(), [], []
    for i, url in enumerate(urls):
        if i:
            time.sleep(0.5)  # be polite
        try:
            url = validate_url(url)
            if not robots.allowed(url):
                errors.append({"url": url, "error": "Disallowed by robots.txt", "kind": "robots"})
                log(f"[content_extractor] robots.txt disallows {url}")
                continue
            log(f"[content_extractor] fetching {url}")
            res = safe_get(url)
            data = extract_page(res.content, res.final_url)
            data.update({"url": url, "final_url": res.final_url, "status_code": res.status,
                         "truncated_response": res.truncated, "fetched_at": utc_now()})
            pages.append(data)
        except ValueError as exc:
            errors.append({"url": url, "error": str(exc), "kind": "rejected"})
        except FetchError as exc:
            errors.append({"url": url, "error": str(exc), "kind": "transient" if exc.transient else "fetch"})
            log(f"[content_extractor] failed {url}: {exc}")

    result = {"pages": pages, "errors": errors, "pages_attempted": len(urls),
              "pages_crawled": len(pages), "extracted_at": utc_now()}
    if out:
        write_json(out, result)
    summary = {"tool": "content_extractor", "pages_attempted": len(urls), "pages_crawled": len(pages),
               "errors": errors}
    if pages:
        summary["status"] = "ok"
        emit(summary if out else {**summary, **result})
        return EXIT_OK
    summary["status"] = "error"
    emit(summary)
    kinds = {e["kind"] for e in errors}
    if kinds == {"robots"}:
        return EXIT_ROBOTS
    if kinds == {"rejected"}:
        return EXIT_USAGE
    return EXIT_TRANSIENT if "transient" in kinds else EXIT_FAIL


if __name__ == "__main__":
    sys.exit(main())
