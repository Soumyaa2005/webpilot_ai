/* WebPilot AI frontend. All server/website text is inserted with textContent (never innerHTML). */
(() => {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const EXAMPLES = [
    ["Crawl a site and return JSON", "Crawl https://example.com, extract important information, summarize the website, and return the result in JSON."],
    ["List headings and links", "Find the main headings and links on https://www.python.org (single page)."],
    ["Find public contact details", "Extract publicly available contact information from https://www.iana.org"],
    ["Compare up to three pages", "Compare information from up to 3 pages of https://www.rfc-editor.org"],
  ];
  const STEPS = [
    ["URL validation", "Blocks local and private addresses before anything runs"],
    ["Website discovery", "site_discovery finds internal pages and honours robots.txt"],
    ["Content extraction", "content_extractor fetches each page and pulls out text"],
    ["Data processing", "data_processor cleans it and writes a summary from that text"],
    ["JSON validation", "json_validator checks required fields and types"],
    ["Response generation", "Everything shown here comes from the validated result"],
  ];
  const LABEL = { pending: "Pending", running: "Running", completed: "Completed", failed: "Failed",
                  partial_success: "Partial Success", skipped: "Skipped", timeout: "Timed out" };
  let busy = false, pollTimer = null;

  function el(tag, cls, text, attrs) {
    const n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text !== undefined && text !== null) n.textContent = text;
    if (attrs) for (const [k, v] of Object.entries(attrs)) n.setAttribute(k, v);
    return n;
  }
  function link(url, text) {
    if (!/^https?:\/\//i.test(url)) return el("span", "", text || url);
    return el("a", "", text || url, { href: url, target: "_blank", rel: "noopener noreferrer" });
  }
  const st = (s) => el("span", "st " + s, LABEL[s] || s);
  const fmtMs = (ms) => ms == null ? "—" : ms < 1000 ? ms + " ms" : (ms / 1000).toFixed(1) + " s";
  const btn = (cls, text, onClick) => { const b = el("button", cls, text, { type: "button" }); if (onClick) b.addEventListener("click", onClick); return b; };

  /* ---------------- welcome ---------------- */
  function buildWelcome() {
    const w = el("div", "welcome"); w.id = "welcome";
    w.append(el("h1", "", "From Natural Language to Real Execution."),
      el("p", "lede", "Describe a website task in plain English. WebPilot plans it, runs approved command-line tools, validates the output and shows every command, exit code and log."));
    w.append(el("h2", "", "Every request goes through six checks"));
    const ol = el("ol", "check");
    STEPS.forEach(([name, what], i) => {
      const li = el("li", "pending"); li.append(el("span", "box"), (() => { const l = el("div", "line"); l.append(el("span", "name", name)); return l; })(), el("div", "sub", what));
      ol.append(li);
    });
    w.append(ol, el("h2", "", "Start with an example"));
    const t = el("div", "try");
    EXAMPLES.forEach(([what, prompt]) => {
      const b = btn("", "", () => send(prompt));
      b.append(el("span", "what", what), el("span", "prompt", prompt)); t.append(b);
    });
    w.append(t);
    return w;
  }
  function renderSideExamples() {
    const ul = $("sideExamples"); ul.textContent = "";
    EXAMPLES.forEach(([what, prompt]) => {
      const li = el("li"), b = btn("", "", () => { $("sidebar").classList.remove("open"); send(prompt); });
      b.append(el("span", "t", what)); li.append(b); ul.append(li);
    });
  }

  /* ---------------- recent activity ---------------- */
  const loadRecent = () => { try { return JSON.parse(localStorage.getItem("wp_recent") || "[]"); } catch { return []; } };
  function saveRecent(id, title, status) {
    const list = loadRecent().filter((r) => r.id !== id);
    list.unshift({ id, title, status });
    try { localStorage.setItem("wp_recent", JSON.stringify(list.slice(0, 12))); } catch { /* ignore */ }
    renderRecent();
  }
  function renderRecent() {
    const ul = $("recent"), list = loadRecent(); ul.textContent = "";
    if (!list.length) { ul.append(el("li", "faint", "No tasks yet")); return; }
    list.forEach((r) => {
      const li = el("li"), b = btn("", "", () => openTask(r.id));
      b.append(el("span", "dot " + r.status), el("span", "t", r.title)); li.append(b); ul.append(li);
    });
  }
  async function openTask(id) {
    $("sidebar").classList.remove("open");
    const r = await fetch("/api/tasks/" + id);
    if (!r.ok) { alert("That task is no longer available."); return; }
    const t = await r.json();
    resetChat();
    const turn = addTurn(t.message || "(task)");
    renderTask(t); renderFinal(turn, t);
  }

  /* ---------------- chat plumbing ---------------- */
  function resetChat() {
    if (pollTimer) clearTimeout(pollTimer);
    $("messages").textContent = "";
    $("tab-exec").replaceChildren(el("p", "empty", "No task running. Send a request to see the plan, tool activity, exit codes and logs here."));
    $("tab-result").replaceChildren(el("p", "empty", "Results appear here when a task finishes."));
    setBusy(false);
  }
  function newChat() { resetChat(); $("messages").append(buildWelcome()); }
  const scrollDown = () => { const m = $("messages"); m.scrollTop = m.scrollHeight; };
  function addTurn(text) {
    const w = $("welcome"); if (w) w.remove();
    const turn = el("article", "turn"), reply = el("div", "reply");
    turn.append(el("p", "ask", text), reply);
    $("messages").append(turn); scrollDown();
    return reply;
  }
  function typing(text) {
    const d = el("div", "typing"); d.append(el("span", "box"), el("span", "", text));
    d.firstChild.parentElement.classList.add("running"); return d;
  }
  function showTab(name) {
    document.querySelectorAll(".tab").forEach((x) => x.classList.toggle("active", x.dataset.tab === name));
    $("tab-exec").classList.toggle("hidden", name !== "exec");
    $("tab-result").classList.toggle("hidden", name !== "result");
  }
  function setBusy(v) { busy = v; $("send").disabled = v; $("input").disabled = v; if (!v) $("input").focus(); }

  async function send(text) {
    text = (text || "").trim();
    if (!text || busy) return;
    setBusy(true); showTab("exec"); $("input").value = ""; autosize();
    const reply = addTurn(text); reply.append(typing("Planning the task…"));
    let resp, data = null;
    try {
      resp = await fetch("/api/chat", { method: "POST", headers: { "Content-Type": "application/json" },
                                        body: JSON.stringify({ message: text, wait: false }) });
    } catch { reply.replaceChildren(errorSection(["Could not reach the WebPilot backend. Start it with: python -m uvicorn backend.main:app"])); setBusy(false); return; }
    try { data = await resp.json(); } catch { /* ignore */ }
    if (!resp.ok && !(data && data.task_id)) {
      const detail = data && typeof data.detail === "string" ? data.detail : "The request was not accepted (HTTP " + resp.status + ").";
      reply.replaceChildren(errorSection([detail])); setBusy(false); return;
    }
    renderTask(data);
    if (!resp.ok) { renderFinal(reply, data); saveRecent(data.task_id, text.slice(0, 46), data.status); setBusy(false); return; }
    reply.replaceChildren(typing("Running. Live status is in the Execution panel."));
    saveRecent(data.task_id, text.slice(0, 46), "running");
    poll(data.task_id, reply, text);
  }
  function poll(id, reply, text) {
    fetch("/api/tasks/" + id).then((r) => r.json()).then((t) => {
      renderTask(t);
      if (t.status === "pending" || t.status === "running") { pollTimer = setTimeout(() => poll(id, reply, text), 600); return; }
      renderFinal(reply, t); saveRecent(id, text.slice(0, 46), t.status); setBusy(false);
    }).catch(() => { reply.replaceChildren(errorSection(["Lost the connection while checking task status. Reload and open it from Recent activity."])); setBusy(false); });
  }

  /* ---------------- execution panel ---------------- */
  function checkItem(s, i) {
    const li = el("li", s.status);
    li.append(el("span", "box", s.status === "completed" ? "✓" : s.status === "failed" ? "✕" : ""));
    const line = el("div", "line"); line.append(el("span", "name", s.label), el("span", "lead"), st(s.status));
    li.append(line);
    const meta = [s.duration_ms != null ? fmtMs(s.duration_ms) : "", s.detail].filter(Boolean).join("  ");
    if (meta) li.append(el("div", "sub", meta));
    return li;
  }
  function renderTask(t) {
    const root = $("tab-exec"); root.textContent = "";
    const intent = t.intent || {};
    const head = el("section"), h = el("h2"); h.append(el("span", "mono", "Task " + t.task_id.slice(0, 8)), st(t.status)); head.append(h);
    const kv = el("dl", "kv");
    [["Request", t.message], ["Type", intent.task_type], ["Target", intent.url], ["Max pages", intent.max_pages],
     ["Planner", intent.source === "groq" ? "Groq, schema-validated" : "Rule-based"], ["Duration", fmtMs(t.duration_ms)]]
      .forEach(([k, v]) => { if (v !== undefined && v !== null && v !== "") kv.append(el("dt", "", k), el("dd", "", String(v))); });
    head.append(kv);
    (intent.notes || []).forEach((n) => head.append(el("p", "note", n)));
    root.append(head);

    const plan = el("section"); plan.append(el("h2", "", "Execution plan"));
    const ol = el("ol", "check"); (t.execution_steps || []).forEach((s, i) => ol.append(checkItem(s, i))); plan.append(ol); root.append(plan);

    const tools = el("section"); tools.append(el("h2", "", "Tool activity"));
    if (!(t.cli_results || []).length) tools.append(el("p", "faint", "No tool has started yet."));
    const ul = el("ul", "runs");
    (t.cli_results || []).forEach((c) => {
      const li = el("li"), top = el("div", "line");
      top.append(el("span", "tool", c.tool + (c.attempt > 1 ? " retry " + (c.attempt - 1) : "")), el("span", "lead"), st(c.status));
      const facts = el("div", "facts");
      const ex = el("span", "", "exit "); ex.append(el("b", "", c.exit_code == null ? "—" : String(c.exit_code)));
      facts.append(ex, el("span", "", fmtMs(c.duration_ms)));
      const d = el("details"); d.append(el("summary", "", "stdout and stderr"),
        el("pre", "", "stdout\n" + (c.stdout || "(empty)")), el("pre", "", "stderr\n" + (c.stderr || "(empty)")));
      li.append(top, facts, el("div", "cmd", "$ " + c.command), d); ul.append(li);
    });
    tools.append(ul); root.append(tools);

    if ((t.warnings || []).length || (t.errors || []).length) {
      const n = el("section");
      (t.warnings || []).forEach((w) => n.append(el("p", "note", w)));
      (t.errors || []).forEach((e) => n.append(el("p", "note err", e)));
      root.append(n);
    }
    if (t.status !== "pending" && t.status !== "running") renderResultPanel(t);
  }

  /* ---------------- results ---------------- */
  const jsonText = (t) => JSON.stringify(t.extracted_data || {}, null, 2);
  function jsonViewer(t) {
    const sec = el("section"), h = el("h2"); h.append(el("span", "", "Structured JSON"),
      el("span", "st " + (t.json_valid ? "completed" : "failed"), t.json_valid ? "Validated" : "Not validated"));
    sec.append(h);
    const tools = el("div", "tools");
    const copy = btn("btn", "Copy JSON", async () => {
      try { await navigator.clipboard.writeText(jsonText(t)); copy.textContent = "Copied"; } catch { copy.textContent = "Copy failed"; }
      setTimeout(() => (copy.textContent = "Copy JSON"), 1500);
    });
    const dl = btn("btn", "Download JSON", () => {
      const a = el("a", "", "", { href: URL.createObjectURL(new Blob([jsonText(t)], { type: "application/json" })), download: "webpilot-" + t.task_id.slice(0, 8) + ".json" });
      document.body.append(a); a.click(); a.remove();
    });
    tools.append(copy, dl); sec.append(tools, el("pre", "json", jsonText(t)));
    return sec;
  }
  function renderResultPanel(t) {
    const root = $("tab-result"); root.textContent = "";
    const d = t.extracted_data || {};
    if (!d.url) { root.append(el("p", "empty", t.status === "failed" ? "No data was extracted. The Execution tab shows which step failed." : "No results.")); return; }
    const v = el("section"), vh = el("h2"); vh.append(el("span", "", "Validation"), el("span", "st " + (t.json_valid ? "completed" : "failed"), t.json_valid ? "Passed" : "Failed"));
    v.append(vh, el("p", "faint", t.json_valid ? "json_validator confirmed the required fields and types." : "Validation did not pass: " + (t.errors || []).join("; ")));
    root.append(v);
    const s = el("section"); s.append(el("h2", "", "Website summary"), el("p", "", t.summary || "—")); root.append(s);
    if (t.ai_interpretation) { const a = el("section"); a.append(el("h2", "", "AI interpretation"), el("p", "", t.ai_interpretation.text), el("p", "faint", t.ai_interpretation.label)); root.append(a); }
    const pg = el("section"); pg.append(el("h2", "", "Pages crawled (" + (d.pages_crawled || 0) + ")"));
    const pl = el("ul", "pagelist");
    (d.pages || []).forEach((p) => { const li = el("li"); li.append(el("div", "", p.title || "(untitled)"), link(p.url), el("div", "faint", p.heading_count + " headings, " + p.paragraph_count + " paragraphs, " + p.word_count + " words")); pl.append(li); });
    pg.append(pl); root.append(pg);
    const src = el("section"); src.append(el("h2", "", "Source URLs"));
    const ul = el("ul", "bul"); (d.sources || []).forEach((u) => { const li = el("li"); li.append(link(u)); ul.append(li); }); src.append(ul); root.append(src);
    root.append(jsonViewer(t));
  }

  /* ---------------- chat answer ---------------- */
  function sec(title, note, cls) {
    const s = el("section", "sec" + (cls ? " " + cls : "")), h = el("h3", "", title);
    if (note) h.append(el("small", "", note));
    s.append(h); return s;
  }
  function errorSection(errs, hint) {
    const s = sec("Could not complete", "", "stop");
    errs.forEach((e) => s.append(el("p", "", e)));
    if (hint) s.append(el("p", "faint", hint));
    return s;
  }
  function renderFinal(reply, t) {
    reply.textContent = "";
    const d = t.extracted_data || {};
    const head = el("div", "runhead");
    head.append(st(t.status), el("span", "mono", "task " + t.task_id.slice(0, 8)), el("span", "", fmtMs(t.duration_ms)));
    reply.append(head);
    if (!d.url) {
      reply.append(errorSection(t.errors && t.errors.length ? t.errors : ["The task did not produce any data."],
        (t.cli_results || []).length ? "Open the Execution panel to see the failing step and its logs." : "Change the request and run it again."));
      return;
    }
    const sum = sec("Summary", "written from the crawled text"); sum.append(el("p", "summary", t.summary || "—")); reply.append(sum);
    if (t.status === "partial_success" || (t.warnings || []).length || (t.errors || []).length) {
      const w = sec("Notes", "", "hold");
      [...(t.warnings || []), ...(t.errors || [])].forEach((x) => w.append(el("p", "", x)));
      reply.append(w);
    }
    const info = sec("Extracted information", "taken directly from the website");
    const kv = el("dl", "kv");
    kv.append(el("dt", "", "Title"), el("dd", "", d.title || "—"), el("dt", "", "Description"), el("dd", "", d.meta_description || "—"),
              el("dt", "", "Pages crawled"), el("dd", "", String(d.pages_crawled)));
    info.append(kv);
    if ((d.headings || []).length) { info.append(el("p", "subhead", "Main headings")); const ul = el("ul", "bul"); d.headings.slice(0, 8).forEach((h) => ul.append(el("li", "", "H" + h.level + ": " + h.text))); info.append(ul); }
    if ((d.paragraphs || []).length) { info.append(el("p", "subhead", "Important paragraphs")); const ul = el("ul", "bul"); d.paragraphs.slice(0, 3).forEach((p) => ul.append(el("li", "", p.text))); info.append(ul); }
    if ((d.internal_links || []).length) { info.append(el("p", "subhead", "Relevant links")); const ul = el("ul", "bul"); d.internal_links.slice(0, 6).forEach((l) => { const li = el("li"); li.append(link(l.url, l.text || l.url)); ul.append(li); }); info.append(ul); }
    if (d.contacts) { info.append(el("p", "subhead", "Public contact details")); info.append(el("p", "", "Emails: " + ((d.contacts.emails || []).join(", ") || "none found") + ". Phones: " + ((d.contacts.phones || []).join(", ") || "none found") + ".")); }
    reply.append(info);
    const src = sec("Sources", "one URL per crawled page"), ul = el("ul", "bul");
    (d.sources || []).forEach((u) => { const li = el("li"); li.append(link(u)); ul.append(li); }); src.append(ul); reply.append(src);
    if (t.ai_interpretation) { const a = sec("AI interpretation", "generated by " + t.ai_interpretation.model + ", not extracted from the site", "ai"); a.append(el("p", "", t.ai_interpretation.text)); reply.append(a); }
    const jb = sec("Structured JSON", t.json_valid ? "validated by json_validator" : "validation failed");
    const det = el("details"); if ((t.intent || {}).json_required !== false) det.open = true;
    det.append(el("summary", "", "Show or hide JSON"), el("pre", "json", jsonText(t))); jb.append(det); reply.append(jb);
    scrollDown();
  }

  /* ---------------- wiring ---------------- */
  function autosize() { const i = $("input"); i.style.height = "auto"; i.style.height = Math.min(i.scrollHeight, 140) + "px"; }
  $("composer").addEventListener("submit", (e) => { e.preventDefault(); send($("input").value); });
  $("input").addEventListener("keydown", (e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send($("input").value); } });
  $("input").addEventListener("input", autosize);
  $("newChat").addEventListener("click", () => { $("sidebar").classList.remove("open"); newChat(); });
  $("menuBtn").addEventListener("click", () => $("sidebar").classList.toggle("open"));
  $("panelBtn").addEventListener("click", () => $("panel").classList.toggle("collapsed"));
  document.querySelectorAll(".tab").forEach((b) => b.addEventListener("click", () => showTab(b.dataset.tab)));
  fetch("/api/health").then((r) => r.json()).then((h) => {
    $("healthInfo").textContent = "Backend online, " + h.tools.length + " approved tools, " + (h.groq_configured ? "Groq enabled" : "rule-based planning");
  }).catch(() => { $("healthInfo").textContent = "Backend unreachable"; });
  renderSideExamples(); renderRecent(); newChat();
})();
