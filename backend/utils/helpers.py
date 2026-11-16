"""Shared helpers: paths, JSON IO, CLI exit codes and text utilities."""
from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]

# CLI exit-code contract shared by every tool and the orchestrator.
EXIT_OK = 0
EXIT_FAIL = 1          # permanent failure
EXIT_USAGE = 2         # bad arguments / rejected input (e.g. blocked URL)
EXIT_ROBOTS = 3        # robots.txt disallows the request
EXIT_TRANSIENT = 75    # transient failure (timeout, 5xx) - safe to retry

TASK_ID_RE = re.compile(r"^[a-f0-9]{32}$")
_CTRL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def outputs_dir() -> Path:
    return Path(os.environ.get("WEBPILOT_OUTPUTS_DIR", PROJECT_ROOT / "outputs")).resolve()


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def safe_path(path_str: str) -> Path:
    """Resolve a path and make sure it lives inside the outputs directory."""
    base = outputs_dir()
    p = Path(path_str)
    p = (p if p.is_absolute() else Path.cwd() / p).resolve()
    if p != base and base not in p.parents:
        raise ValueError(f"path must be inside the outputs directory ({base})")
    return p


def task_dir(task_id: str) -> Path:
    if not TASK_ID_RE.match(task_id):
        raise ValueError("invalid task id")
    d = outputs_dir() / task_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def read_json(path: Path) -> Any:
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def normalize_ws(text: str) -> str:
    return re.sub(r"\s+", " ", _CTRL_RE.sub(" ", text or "")).strip()


def truncate(text: str, limit: int) -> str:
    text = text or ""
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def log(msg: str) -> None:
    """Diagnostics go to stderr; stdout is reserved for the JSON result."""
    print(msg, file=sys.stderr, flush=True)


def emit(obj: Any) -> None:
    print(json.dumps(obj, ensure_ascii=False), flush=True)


def bootstrap_path() -> None:  # pragma: no cover - used by tool scripts
    root = str(PROJECT_ROOT)
    if root not in sys.path:
        sys.path.insert(0, root)
