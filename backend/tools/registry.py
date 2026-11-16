"""Approved CLI tool registry. Only tools listed here can ever be executed,
and only with flags from each tool's allowlist. No shell is ever involved."""
from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parent


class ToolNotApproved(Exception):
    pass


@dataclass(frozen=True)
class ToolSpec:
    name: str
    script: str
    description: str
    allowed_flags: frozenset
    timeout: int  # seconds, enforced by the orchestrator


REGISTRY: dict[str, ToolSpec] = {
    "site_discovery": ToolSpec(
        "site_discovery", "site_discovery.py",
        "Discover internal pages of a website (robots.txt aware).",
        frozenset({"--url", "--max-pages", "--output"}), 45),
    "content_extractor": ToolSpec(
        "content_extractor", "content_extractor.py",
        "Fetch pages and extract title, meta, headings, paragraphs and links.",
        frozenset({"--discovery-file", "--url", "--max-pages", "--output"}), 100),
    "data_processor": ToolSpec(
        "data_processor", "data_processor.py",
        "Clean, de-duplicate and organise extracted data; build a factual summary.",
        frozenset({"--input", "--output", "--fields"}), 20),
    "json_validator": ToolSpec(
        "json_validator", "json_validator.py",
        "Validate JSON syntax, required fields and types.",
        frozenset({"--input", "--output"}), 20),
}


def build_command(tool: str, args: dict[str, str]) -> list[str]:
    """Build an argv list (never a shell string) from allowlisted flags only."""
    spec = REGISTRY.get(tool)
    if spec is None:
        raise ToolNotApproved(f"'{tool}' is not an approved tool")
    cmd = [sys.executable, "-B", str(TOOLS_DIR / spec.script)]
    for flag, value in args.items():
        if flag not in spec.allowed_flags:
            raise ToolNotApproved(f"flag '{flag}' is not allowed for tool '{tool}'")
        cmd += [flag, str(value)]
    return cmd


def describe() -> list[dict]:
    return [{"name": s.name, "description": s.description, "timeout_s": s.timeout} for s in REGISTRY.values()]
