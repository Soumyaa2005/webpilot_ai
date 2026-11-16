#!/usr/bin/env python3
"""CLI tool 1: discover internal pages of a website (robots.txt aware).

Usage: python backend/tools/site_discovery.py --url https://example.com \
           --max-pages 3 --output outputs/demo/discovery.json
"""
import argparse
import sys
from pathlib import Path
from urllib.parse import urldefrag, urljoin, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from bs4 import BeautifulSoup  # noqa: E402

from backend.security import (FetchError, RobotsChecker, UrlRejected, safe_get,  # noqa: E402
                              validate_url)
from backend.utils.helpers import (EXIT_FAIL, EXIT_OK, EXIT_ROBOTS, EXIT_TRANSIENT,  # noqa: E402
                                   EXIT_USAGE, emit, log, normalize_ws, safe_path,
                                   truncate, utc_now, write_json)

SKIP_EXT = (".pdf", ".jpg", ".jpeg", ".png", ".gif", ".svg", ".webp", ".ico", ".zip", ".gz",
            ".tar", ".mp3", ".mp4", ".avi", ".mov", ".css", ".js", ".json", ".xml", ".exe",
            ".dmg", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".woff", ".woff2")


def host_key(url: str) -> str:
    h = (urlparse(url).hostname or "").lower()
    return h[4:] if h.startswith("www.") else h


def normalize(url: str) -> str:
    p = urlparse(urldefrag(url)[0])
    path = p.path or "/"
    if len(path) > 1 and path.endswith("/"):
        path = path.rstrip("/")
    return p._replace(netloc=p.netloc.lower(), path=path).geturl()


def discover(url: str, max_pages: int) -> dict:
    start = validate_url(url)
    robots = RobotsChecker()
    if not robots.allowed(start):
        raise PermissionError("robots.txt disallows fetching this URL")
    res = safe_get(start)
    soup = BeautifulSoup(res.content, "html.parser")
    final = res.final_url
    base_host = host_key(final)
    seen = {normalize(final)}
    found, skipped = [], []
    for a in soup.find_all("a", href=True):
        href = (a.get("href") or "").strip()
        if not href or href.startswith(("#", "javascript:", "mailto:", "tel:", "data:")):
            continue
        absolute = urljoin(final, href)
        p = urlparse(absolute)
        if p.scheme not in ("http", "https") or host_key(absolute) != base_host:
            continue
        if p.path.lower().endswith(SKIP_EXT):
            continue
        norm = normalize(absolute)
        if norm in seen:
            continue
        seen.add(norm)
        if not robots.allowed(norm):
            skipped.append(norm)
            continue
        found.append({"url": norm, "anchor_text": truncate(normalize_ws(a.get_text(" ")), 120)})
        if len(found) >= max_pages:
            break
    return {
        "start_url": start,
        "final_url": final,
        "status_code": res.status,
        "discovered": found,
        "discovered_count": len(found),
        "skipped_by_robots": skipped,
        "truncated_response": res.truncated,
        "fetched_at": utc_now(),
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Discover internal pages of a website")
    ap.add_argument("--url", required=True)
    ap.add_argument("--max-pages", type=int, default=3)
    ap.add_argument("--output", help="write full JSON result to this file (inside outputs/)")
    args = ap.parse_args(argv)
    args.max_pages = max(1, min(args.max_pages, 10))
    try:
        out = safe_path(args.output) if args.output else None
        log(f"[site_discovery] discovering {args.url}")
        result = discover(args.url, args.max_pages)
    except ValueError as exc:  # UrlRejected is a ValueError; safe_path too
        log(f"[site_discovery] rejected: {exc}")
        emit({"tool": "site_discovery", "status": "error", "error": str(exc)})
        return EXIT_USAGE
    except PermissionError as exc:
        log(f"[site_discovery] {exc}")
        emit({"tool": "site_discovery", "status": "error", "error": str(exc)})
        return EXIT_ROBOTS
    except FetchError as exc:
        log(f"[site_discovery] fetch failed: {exc}")
        emit({"tool": "site_discovery", "status": "error", "error": str(exc), "transient": exc.transient})
        return EXIT_TRANSIENT if exc.transient else EXIT_FAIL
    if out:
        write_json(out, result)
        emit({"tool": "site_discovery", "status": "ok", "discovered_count": result["discovered_count"],
              "final_url": result["final_url"], "output_file": str(out)})
    else:
        emit({"tool": "site_discovery", "status": "ok", **result})
    log(f"[site_discovery] found {result['discovered_count']} internal page(s)")
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
