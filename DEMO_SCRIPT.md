# WebPilot AI — Demo video script (≈6 min) and technical explanation

## Before recording
`python -m uvicorn backend.main:app --port 8000`, open http://127.0.0.1:8000. Have a terminal visible. Add a Groq key to `.env` only if you want to show the LLM planner (the app works without it).

## Script
**0:00–0:40 — Hook.** "Most AI apps *talk* about doing work. WebPilot AI actually executes it. I give it plain English; it plans, runs real command-line tools, validates the output and shows me the evidence."

**0:40–1:30 — Architecture (show README Mermaid diagram).** Chat → FastAPI → intent → planner → orchestrator → approved CLI registry → 4 tools → JSON validator → result. "The model can classify intent, but it can never choose commands. Only four allow-listed tools exist."

**1:30–2:15 — The tools are real CLIs.** In the terminal run:
`python backend/tools/site_discovery.py --url https://example.com --output outputs/demo/discovery.json` then `python backend/tools/json_validator.py --input outputs/demo/processed.json; echo $?`. Point out stdout JSON, stderr logs and exit codes.

**2:15–4:00 — Main demo.** Click "Full crawl + JSON" (or paste: *Crawl https://example.com, extract important information, summarize the website, and return the result in JSON.*). Narrate the Execution panel as steps turn green *because the backend says so*. Open a tool card: command, exit 0, duration, stdout/stderr. Show chat answer: summary, extracted info, sources, validated JSON. Open Results tab: Copy / Download JSON, validation badge.

**4:00–5:00 — Honesty and failure.** Try `Crawl http://localhost:8000` → rejected (SSRF). Try a non-existent domain → honest failure, nothing fabricated, later steps "Skipped". Mention robots.txt and retry on transient errors.

**5:00–5:45 — Security + tests.** `python -m pytest tests -q` → 77 passing. Mention no `shell=True`, flag allow-lists, redirect re-validation, untrusted website text.

**5:45–6:00 — Close.** "From natural language to real execution — with evidence."

## Technical explanation (for the judges)
**Why it differs from a chatbot.** Every answer is produced by programs that really ran. The UI shows commands, exit codes, stdout/stderr and timing; the summary is built from text that was actually fetched. A failed fetch yields a failed task, not a plausible story.

**Natural language → structured plan.** `intent.py` asks Groq (if configured) for a JSON intent, validated by a strict Pydantic schema (`extra="forbid"`, enumerated task types and steps, ordered steps). The URL must literally occur in the user's message. Any violation, API failure or missing key falls back to deterministic rules (URL regex, keyword task types, page-count parsing). `planner.py` then emits the fixed approved pipeline: validate_url → discover_pages → extract_content → process_data → validate_json → generate_summary.

**Tool selection.** `orchestrator.py` maps each step to a tool in `tools/registry.py`. The registry knows script paths, allowed flags and timeouts; unknown tools or flags raise `ToolNotApproved`.

**Subprocess execution.** `subprocess.run([python, -B, script, flags...], shell=False, capture_output=True, timeout=…, env=minimal)`. Arguments are separate argv entries, so even a hostile string stays inert. Stages exchange data through task-scoped JSON files under `outputs/<task_id>/`.

**Capturing results.** For every attempt we store tool, command, attempt number, stdout, stderr, exit code, duration and status (`completed/failed/timeout`). Exit code 75 (transient) and timeouts trigger up to two retries with backoff; each retry appears as its own entry.

**JSON validation.** `json_validator.py` checks syntax (with line/column), required fields, strict types via Pydantic, and consistency (`pages_crawled == len(sources)`). The task is only "Completed" if the validator exits 0 and no page failed; otherwise it is "Partial Success" or "Failed" with the reasons listed.

**Errors and security.** Blocked URLs return HTTP 400 with a task record; crawl failures return a failed task with the failing step marked and later steps skipped; unexpected exceptions return a generic message while details go to redacted server logs. SSRF checks run on the initial URL and on each redirect; robots.txt, size/time/page limits and untrusted-content handling are enforced as described in the README.
