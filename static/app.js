"use strict";

const state = {
  token: null,
  operator: null,
  batchId: null,
  batch: null,
  selected: null,
  proposalId: null,
  pollTimer: null,
  callTimer: null,
  callId: null,
  callDeadline: 0,
  lang: "en",
};

try {
  state.token = sessionStorage.getItem("nipta.token");
  state.operator = sessionStorage.getItem("nipta.operator");
  const saved = localStorage.getItem("nipta.lang");
  state.lang = ["en", "kn", "hi"].includes(saved) ? saved : "en";  // a stale value would break every request
} catch (_) { /* storage unavailable */ }

const $ = (sel) => document.querySelector(sel);
const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

function inr(paise) {
  if (paise === null || paise === undefined) return "—";
  const neg = paise < 0;
  const abs = Math.abs(paise);
  const rupees = Math.floor(abs / 100);
  const p = abs % 100;
  let s = String(rupees);
  if (s.length > 3) {
    let head = s.slice(0, -3);
    const tail = s.slice(-3);
    head = head.replace(/\B(?=(\d{2})+(?!\d))/g, ",");
    s = head + "," + tail;
  }
  return (neg ? "-" : "") + "₹" + s + (p ? "." + String(p).padStart(2, "0") : "");
}

function toast(msg) {
  const t = $("#toast");
  t.textContent = msg;
  t.classList.add("show");
  clearTimeout(toast._t);
  toast._t = setTimeout(() => t.classList.remove("show"), 3500);
}

async function api(path, opts = {}) {
  const headers = { ...(opts.headers || {}) };
  if (state.token) headers.Authorization = `Bearer ${state.token}`;
  if (opts.json !== undefined) {
    headers["Content-Type"] = "application/json";
    opts.body = JSON.stringify(opts.json);
  }
  const res = await fetch(path, { ...opts, headers });
  const type = res.headers.get("content-type") || "";
  const body = type.includes("application/json") ? await res.json() : await res.text();
  if (!res.ok) {
    if (res.status === 401 && state.token && path !== "/api/auth/session") signOut();
    const msg = body && body.error ? `${body.error}: ${body.message}`
      : Array.isArray(body && body.detail) ? body.detail.map((d) => `${(d.loc || []).slice(-1)[0]}: ${d.msg.replace(/^Value error, /, "")}`).join("; ")
      : (body.detail || res.statusText);
    throw new Error(msg);
  }
  return body;
}

const lq = () => `lang=${state.lang}`;

// ---- capability badges & session ------------------------------------------

const CAP_LABELS = {
  sarvam_vision: "Extraction", sarvam_chat: "Agent", sarvam_stt: "Voice input", sarvam_tts: "Speech",
  cashfree_payouts: "Payouts", cashfree_webhooks: "Webhooks", cashfree_secure_id: "Secure ID",
};

async function loadHealth() {
  const h = await api("/api/health");
  const caps = h.capabilities;
  state.caps = caps;
  renderDemoTools();
  $("#voiceHint").textContent = caps.sarvam_stt === "LIVE"
    ? "Speak or type in Kannada, Hindi or English. Tap the mic again to send. Asking never pays."
    : "Type in Kannada, Hindi or English (voice is off). Asking never pays.";
  const el = $("#capabilities");
  el.innerHTML = Object.entries(CAP_LABELS).map(([k, label]) => {
    const v = caps[k];
    const cls = v === "LIVE" || v === "SIGNATURE_CHECK_READY" ? "ok" : v === "MISCONFIGURED" ? "bad" : "muted";
    const text = v === "LIVE" ? "live" : v.replace(/^OFF_?/, "").replace(/_/g, " ").toLowerCase() || "off";
    return `<span class="chip ${cls}" title="${esc(v)}">${esc(label)}: ${esc(text)}</span>`;
  }).join("") + (caps.test_mode ? `<span class="chip warn">TEST MODE</span>` : "");
  const live = Object.keys(CAP_LABELS).filter((k) => caps[k] === "LIVE").length;
  const summary = $("#capsSummary");
  summary.textContent = `Cashfree sandbox · ${live}/${Object.keys(CAP_LABELS).length} live`;
  summary.className = `chip ${live ? "ok" : "warn"}`;
}

function renderSession() {
  const signedIn = Boolean(state.token);
  $("#login").hidden = signedIn;
  $("#who").hidden = !signedIn;
  $("#who").innerHTML = signedIn ? `${esc(state.operator)} · <button class="link" id="signout">Sign out</button>` : "";
  if (signedIn) $("#signout").onclick = signOut;
  document.querySelectorAll("[data-auth]").forEach((b) => {
    if (b.tagName === "LABEL") b.classList.toggle("disabled", !signedIn);
    else b.disabled = !signedIn;
  });
  $("#upload").disabled = !signedIn;
}

function stopPolling() {
  clearTimeout(state.pollTimer);
  clearTimeout(state.callTimer);
  state.pollTimer = state.callTimer = null;
}

// Every data route needs a session, so signed out we show only this prompt.
function renderSignedOut() {
  stopPolling();
  Object.assign(state, { batchId: null, batch: null, selected: null, proposalId: null });
  $("#detail").hidden = true;
  $("#approval").hidden = true;
  $("#welcome").hidden = false;
  ["#batchSummary", "#batchSelect", "#today", "#plan", "#activity", "#vendorList"].forEach((s) => { $(s).innerHTML = ""; });
  $("#billList").innerHTML = `<p class="muted" style="font-weight:600;margin-top:18px">Sign in with your PIN to see this Friday's bills.</p>`;
  $("#fridayTile").textContent = "—";
  $("#cashTile").textContent = "—";
}

function signOut() {
  state.token = null;
  state.operator = null;
  try { sessionStorage.removeItem("nipta.token"); sessionStorage.removeItem("nipta.operator"); } catch (_) {}
  renderSession();
  renderSignedOut();
}

$("#login").addEventListener("submit", async (e) => {
  e.preventDefault();
  try {
    const r = await api("/api/auth/session", { method: "POST", json: { pin: $("#pin").value } });
    state.token = r.token;
    state.operator = r.operator_id;
    try { sessionStorage.setItem("nipta.token", r.token); sessionStorage.setItem("nipta.operator", r.operator_id); } catch (_) {}
    $("#pin").value = "";
    renderSession();
    toast("Signed in");
  } catch (err) { toast(err.message); return; }
  loadBatches().catch((e) => toast(e.message));
  loadVendors().catch((e) => toast(e.message));
});

$("#lang").value = state.lang;
$("#lang").addEventListener("change", () => {
  state.lang = $("#lang").value;
  try { localStorage.setItem("nipta.lang", state.lang); } catch (_) {}
  refreshBatch();
  if (state.selected) showInvoice(state.selected);
  if (state.proposalId) loadProposal(state.proposalId);
});

// ---- demo reset (wipes all data, reloads the six demo bills) --------------------

function renderDemoTools() {
  const el = $("#demoTools");
  if (!el) return;
  if (!state.caps || !state.caps.demo_reset) { el.innerHTML = ""; return; }
  el.innerHTML = `<button type="button" class="secondary small" id="demoResetBtn" data-auth>Reset demo data…</button><div id="demoResetBox"></div>`;
  renderSession();
  $("#demoResetBtn").onclick = openDemoReset;
}

async function openDemoReset() {
  const box = $("#demoResetBox");
  let p;
  try { p = await api("/api/demo/reset-preview"); } catch (err) { toast(err.message); return; }
  box.innerHTML = `<form class="inline-ask" id="demoResetForm">
      <label class="small">This removes all ${p.bills} bills (${p.uploads} uploaded), ${p.approved_vendors} vendor approval(s) and
        ${p.payments} payment record(s), then reloads the six demo bills.
        ${p.sandbox_in_flight ? `<b>${p.sandbox_in_flight} Cashfree sandbox payout(s) still in progress will no longer be tracked</b> (nothing is cancelled at Cashfree).` : ""}
        Type <b>RESET</b> to confirm.</label>
      <input class="ask-input" id="demoResetInput" autocomplete="off" placeholder="RESET">
      <div class="ask-row"><button type="submit" class="small danger" id="demoResetGo">Reset demo data</button>
        <button type="button" class="secondary small" id="demoResetCancel">Cancel</button><span class="small bad-text" id="demoResetErr"></span></div>
    </form>`;
  $("#demoResetInput").focus();
  $("#demoResetCancel").onclick = () => { box.innerHTML = ""; };
  $("#demoResetForm").onsubmit = async (e) => {
    e.preventDefault();
    if ($("#demoResetInput").value.trim() !== "RESET") { $("#demoResetErr").textContent = "Type RESET exactly."; return; }
    try {
      const r = await busy($("#demoResetGo"), "Resetting…", () => api("/api/demo/reset", { method: "POST", json: { confirm: "RESET" } }));
      box.innerHTML = "";
      if (typeof stopPolling === "function") stopPolling();
      clearTimeout(state.pollTimer);
      state.selected = null; state.proposalId = null; state.batchId = r.batch_id;
      $("#detail").hidden = true; $("#approval").hidden = true;
      if ($("#welcome")) $("#welcome").hidden = false;
      await loadBatches(r.batch_id);
      await loadVendors();
      const menu = $("#demoTools").closest("details"); if (menu) menu.open = false;
      toast(`Demo data reset: ${r.bills} demo bills loaded.`);
    } catch (err) { $("#demoResetErr").textContent = err.message; }
  };
}

// ---- batches ----------------------------------------------------------------

async function loadBatches(selectId) {
  const batches = await api("/api/batches");
  const sel = $("#batchSelect");
  sel.innerHTML = batches.map((b) =>
    `<option value="${esc(b.batch_id)}">${b.source === "FIXTURE" ? "Demo batch" : "Upload"} · ${b.invoice_count} invoices · ${new Date(b.created_at * 1000).toLocaleTimeString()}</option>`
  ).join("");
  state.batchId = selectId || state.batchId || (batches.find((b) => b.source === "FIXTURE") || batches[0] || {}).batch_id;
  if (state.batchId) sel.value = state.batchId;
  await refreshBatch();
}

$("#batchSelect").addEventListener("change", (e) => {
  state.batchId = e.target.value;
  state.selected = null;
  $("#detail").hidden = true;
  $("#welcome").hidden = false;
  refreshBatch();
});

const DECISION_CLASS = { ELIGIBLE: "ok", REVIEW: "warn", BLOCKED: "bad" };
const OVERLAY_CLASS = { PAID: "ok", PAYMENT_PENDING: "info", PAYMENT_UNKNOWN: "warn", PAYMENT_FAILED: "bad", REVERSED_REVIEW: "bad", SIMULATED: "muted" };

async function refreshBatch() {
  if (!state.batchId) return;
  const b = await api(`/api/batches/${state.batchId}?${lq()}`);
  // Newest first everywhere bills are listed (the plan keeps its own ranking).
  b.invoices.sort((x, y) => (y.ingested_seq || 0) - (x.ingested_seq || 0));
  state.batch = b;
  // Group by where the bill is, not only by its check result: a paid ELIGIBLE bill is no longer "safe to pay".
  const SENT = new Set(["PAID", "PAYMENT_PENDING", "PAYMENT_UNKNOWN", "SIMULATED"]);
  const stage = (i) => (SENT.has(i.payment_overlay) ? "SENT" : i.decision);
  const groups = [["BLOCKED", "bad", "Blocked", "Blocked"], ["REVIEW", "warn", "Needs a check", "Check"],
    ["ELIGIBLE", "ok", "Safe to pay", "Safe"], ["SENT", "info", "Paid & sent", "Sent"]];
  const sum = (list) => list.reduce((t, i) => t + (i.stated_total_paise || 0), 0);
  const by = (k) => b.invoices.filter((i) => stage(i) === k);
  $("#batchSummary").innerHTML = groups.map(([k, cls, label, short]) => `
    <div class="stat"><span class="label"><span class="dot ${cls}"></span>${esc(short)}</span>
      <div class="n">${by(k).length}</div><div class="amt">${by(k).length ? inr(sum(by(k))) : "—"}</div></div>`).join("");
  const bill = (i) => {
    const open = i.findings.find((f) => f.status === "OPEN");
    const ps = i.proposal && i.proposal.state;
    const why = i.payment_overlay === "PAYMENT_FAILED" ? "last payment failed · can be paid again"
      : i.payment_overlay ? i.payment_overlay.replace(/_/g, " ").toLowerCase()
      : open ? open.explanation
      : ps === "AUTHORIZED" ? "approved, ready to send"
      : ps === "READY_FOR_APPROVAL" ? "prepared, ready to approve"
      : `${i.invoice_number || ""}${i.due_date ? ` · due ${i.due_date}` : ""}`;
    const ini = (i.vendor || "?").split(/\s+/).map((w) => w[0]).join("").slice(0, 2).toUpperCase();
    return `<button class="bill ${i.invoice_id === state.selected ? "selected" : ""}" data-id="${esc(i.invoice_id)}">
      <span class="ini" aria-hidden="true">${esc(ini)}</span>
      <span class="name">${esc(i.vendor || "Unknown vendor")}</span><span class="amt">${inr(i.stated_total_paise)}</span>
      <span class="why"><span class="tag ${stage(i) === "SENT" ? "muted" : DECISION_CLASS[i.decision]}">${esc(stage(i) === "SENT" ? (i.payment_overlay === "PAID" ? "Paid" : i.payment_overlay === "SIMULATED" ? "Simulated" : "Sent") : i.decision_label)}</span> ${esc(why)}</span></button>`;
  };
  const recent = b.invoices.filter((i) => i.is_upload).slice(0, 5);  // already newest first
  const when = (ts) => new Date(ts * 1000).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
  const recentHtml = recent.length ? `
    <div class="group-head"><span class="dot info"></span><span class="label">Recently added</span><span class="amt">${recent.length}</span></div>
    <div class="bill-list">${recent.map((i) => bill(i).replace('<span class="why">', `<span class="why"><span class="tag muted">added ${esc(when(i.ingested_at))}</span> `)).join("")}</div>` : "";
  $("#billList").innerHTML = recentHtml + groups.filter(([k]) => by(k).length).map(([k, cls, label]) => `
    <div class="group-head"><span class="dot ${cls}"></span><span class="label">${esc(label)}</span><span class="amt">${inr(sum(by(k)))}</span></div>
    <div class="bill-list">${by(k).map(bill).join("")}</div>`).join("")
    + (b.invoices.length ? `<div class="total-row"><span class="label">All bills</span><span class="v">${inr(sum(b.invoices))}</span></div>`
      : `<p class="muted" style="font-weight:600;margin-top:18px">No bills yet. Add this Friday's bills or load the sample.</p>`)
    + (b.unprocessed_documents.length ? `<p class="small muted">${b.unprocessed_documents.length} not yet checked. Press Run checks.</p>` : "");
  document.querySelectorAll("#billList .bill").forEach((el) => el.onclick = () => showInvoice(el.dataset.id));
  const hasBills = b.invoices.length > 0;
  // Next step gets the green key: add bills, then run checks; after that the Today list leads.
  $("label.file-btn").classList.toggle("primary", !hasBills && !b.unprocessed_documents.length);
  $("#processBatch").classList.toggle("primary", b.unprocessed_documents.length > 0);
  $("#welcome").classList.toggle("has-bills", hasBills);
  $("#introText").hidden = hasBills;
  $("#sixDetails").open = false;
  if (!hasBills) $("#today").innerHTML = "";
  $("#fridayTile").textContent = `${b.invoices.length} bills`;
  $("#cashTile").textContent = inr(b.cash_budget_paise);
  $("#budget").value = Math.round(b.cash_budget_paise / 100);
  loadPlan();
  loadActivity();
}

$("#loadFixtures").addEventListener("click", async () => {
  try {
    const r = await api("/api/batches", { method: "POST", body: new URLSearchParams({ fixture: "true" }) });
    await loadBatches(r.batch_id);
    toast("Demo batch loaded");
  } catch (err) { toast(err.message); }
});

// Run a long action with visible progress on the button that started it.
async function busy(btn, label, fn) {
  const original = btn.textContent;
  btn.disabled = true;
  btn.textContent = label;
  try { return await fn(); }
  finally { btn.textContent = original; renderSession(); }
}

function summaryText(s) {
  const d = s.by_decision;
  return `${d.ELIGIBLE} eligible · ${d.REVIEW} need review · ${d.BLOCKED} blocked`;
}

async function runChecks(batchId) {
  const r = await busy($("#processBatch"), "Reading invoices…", () =>
    api(`/api/batches/${batchId}/process`, { method: "POST" }));
  await loadBatches(batchId);
  const what = r.new_documents ? `Read ${r.new_documents} new document(s).` : "No new documents; re-checked all invoices.";
  toast(`${what} ${summaryText(r.summary)}`);
  const fresh = r.new_invoice_ids || [];
  if (fresh.length === 1) showInvoice(fresh[0]);
  else if (fresh.length > 1) toast(`${fresh.length} new bills checked: see "Recently added" at the top of the list.`);
  return r;
}

$("#upload").addEventListener("change", async (e) => {
  const files = [...e.target.files];
  e.target.value = "";
  if (!files.length) return;
  const fd = new FormData();
  files.forEach((f) => fd.append("files", f));
  try {
    // Add to the list being worked on, so earlier bills stay visible; start a batch only if there is none.
    const url = state.batchId ? `/api/batches/${state.batchId}/documents` : "/api/batches";
    const r = await api(url, { method: "POST", body: fd });
    const dups = r.documents.filter((d) => d.duplicate_upload);
    if (r.rejected && r.rejected.length) toast(`Skipped ${r.rejected.length} file(s): ${r.rejected.map((x) => `${x.name} (${x.reason.replace(/;.*$/, "")})`).join(", ")}. Export iPhone photos as JPEG.`);
    if (r.all_duplicates) {
      await loadBatches(r.batch_id);
      const inv = dups[0].existing_invoice_id;
      toast(`Already uploaded${inv ? ` as ${inv}` : ""}. No second bill was created; showing the existing one.`);
      if (inv) showInvoice(inv);
      return;
    }
    if (dups.length) toast(`${dups.length} file(s) were already uploaded and were skipped.`);
    await loadBatches(r.batch_id);
    await runChecks(r.batch_id);  // read and check straight away; no separate click needed
  } catch (err) { toast(err.message); }
});

$("#processBatch").addEventListener("click", async () => {
  if (!state.batchId) return;
  try { await runChecks(state.batchId); } catch (err) { toast(err.message); }
});

// Inline confirmation instead of window.prompt/confirm, which some browsers and in-app views block
// (they then return null and the click silently does nothing).
function inlineAsk(anchor, { label, placeholder = "", minLength = 0, confirmText = "Save", needsText = true }) {
  return new Promise((resolve) => {
    const host = anchor.closest(".finding") || anchor.parentElement;
    host.querySelectorAll(".inline-ask").forEach((n) => n.remove());
    const box = document.createElement("form");
    box.className = "inline-ask";
    box.innerHTML = `<label class="small">${esc(label)}</label>
      ${needsText ? `<input class="ask-input" placeholder="${esc(placeholder)}" autocomplete="off">` : ""}
      <div class="ask-row"><button type="submit" class="small">${esc(confirmText)}</button>
        <button type="button" class="secondary small ask-cancel">Cancel</button>
        <span class="small bad-text ask-err"></span></div>`;
    host.appendChild(box);
    const input = box.querySelector(".ask-input");
    if (input) input.focus();
    const done = (v) => { box.remove(); resolve(v); };
    box.querySelector(".ask-cancel").onclick = () => done(null);
    box.onsubmit = (e) => {
      e.preventDefault();
      if (!needsText) return done(true);
      const v = input.value.trim();
      if (v.length < minLength) { box.querySelector(".ask-err").textContent = `Please write at least ${minLength} characters.`; return; }
      done(v);
    };
  });
}

// ---- invoice detail -----------------------------------------------------------

async function showInvoice(id) {
  state.selected = id;
  clearTimeout(state.callTimer);
  document.querySelectorAll("#billList .bill").forEach((el) => el.classList.toggle("selected", el.dataset.id === id));
  $("#welcome").hidden = true;
  const d = await api(`/api/invoices/${id}/findings?${lq()}`);
  const el = $("#detail");
  el.hidden = false;
  const bank = d.bank_comparison;
  const findingsHtml = d.findings.length ? d.findings.map((f) => `
    <div class="finding ${f.status === "RESOLVED" ? "RESOLVED" : f.severity}">
      <div class="row">
        <span><span class="chip ${f.severity === "BLOCK" ? "bad" : f.severity === "REVIEW" ? "warn" : "muted"}">${esc(f.severity)}</span>
        <strong class="mono">${esc(f.code)}</strong>${f.matched_invoice_id ? ` → <span class="mono">${esc(f.matched_invoice_id)}</span>` : ""}</span>
        ${f.status === "RESOLVED"
          ? `<span class="small muted">Resolved by ${esc(f.resolved_by)}: ${esc(f.resolved_reason)}</span>`
          : f.severity === "REVIEW" ? `<button class="secondary small" data-resolve="${esc(f.finding_id)}" data-auth>Mark reviewed</button>` : `<span class="small muted">No override</span>`}
      </div>
      <p>${esc(f.explanation)}</p>
      <details><summary>Evidence</summary><pre>${esc(JSON.stringify(f.evidence, null, 2))}</pre></details>
    </div>`).join("") : `<p class="muted">No findings.</p>`;
  // The design's six checks, each fed by the finding codes that belong to it.
  const CHECKS = [
    ["Adds up", ["ARITHMETIC_MISMATCH", "ARITHMETIC_UNVERIFIABLE"], "Line items add up to the stated total."],
    ["Words match", ["WORDS_FIGURES_MISMATCH", "WORDS_UNPARSABLE"], "Amount in words matches the figure."],
    ["Not twice", ["CONFIRMED_DUPLICATE", "POSSIBLE_DUPLICATE", "ALREADY_PAID", "PAYMENT_IN_FLIGHT"], "Not already paid, not twice in this batch."],
    ["Known vendor", ["VENDOR_UNRESOLVED"], "Vendor is in your approved list."],
    ["Same bank account", ["CHANGED_BENEFICIARY", "NO_APPROVED_BENEFICIARY"], "Pays only your saved account."],
    ["Name confirmed", ["VERIFICATION_MISSING", "VERIFICATION_FAILED", "ACCOUNT_NAME_MISMATCH"], "Account holder's name matches."],
  ];
  const open = d.findings.filter((f) => f.status === "OPEN");
  const unread = open.some((f) => f.code === "DOCUMENT_UNREADABLE");  // nothing was checked, so nothing "passed"
  const checkCards = CHECKS.map(([title, codes, ok]) => {
    const hits = open.filter((f) => codes.includes(f.code));
    // Never show a seeded verification as a real Cashfree name match.
    const seeded = title === "Name confirmed" && !hits.length && (state.caps || {}).cashfree_secure_id !== "LIVE";
    if (seeded) ok = "Seeded demo record, not a live Cashfree check.";
    const st = unread ? ["NOT RUN", "muted"] : hits.some((f) => f.severity === "BLOCK") ? ["HOLD", "bad"]
      : hits.length ? ["CHECK", "warn"] : seeded ? ["SEEDED", "muted"] : ["PASS", "ok"];
    return `<div class="check ${st[0] === "HOLD" ? "hold" : ""}"><div class="row"><span class="t">${esc(title)}</span>
      <span class="chip ${st[1]}">${st[0]}</span></div><p>${esc(hits.length ? hits.map((f) => f.explanation).join(" ") : unread ? "Bill could not be read." : ok)}</p></div>`;
  });
  const passed = checkCards.filter((c) => c.includes(">PASS<")).length;
  const verdictCls = DECISION_CLASS[d.decision];
  const verdictHtml = `<div class="verdict ${verdictCls}"><div><div class="big">${esc(d.decision_label)}</div>
    <p>${esc(d.explanation || "")}</p></div></div>`;
  const checksHtml = `<div class="panel-head" style="margin-top:22px"><h2>Six checks</h2>
    <span class="small muted" style="font-weight:600">${passed} passed · ${6 - passed} not passed</span></div>
    <div class="checks">${checkCards.join("")}</div>`;
  // Vendor call (Relay): only offered when the printed bank account differs from the saved one.
  const vc = d.vendor_call;
  const callHtml = d.findings.some((f) => f.code === "CHANGED_BENEFICIARY" && f.status !== "RESOLVED") ? `
    <h3>Vendor call</h3>
    <div class="finding">
      <div class="row">
        <span>${vc ? `<span class="chip ${vc.status === "DENIED_CHANGE" ? "bad" : "warn"}">${esc(vc.status)}</span> ${esc(vc.text)}`
                   : `<span class="muted">Ask the vendor, on the number we have on file, whether this account is theirs.</span>`}</span>
        <button class="secondary small" id="vendorCall" data-auth>${vc ? "Call again" : "Call vendor"}</button>
      </div>
      ${vc && vc.summary ? `<p class="small">${esc(vc.summary)}</p>` : ""}
    </div>` : "";
  const lines = d.lines.map((l) => `<tr><td>${esc(l.description)}</td><td class="num">${l.quantity_milli === null ? "—" : l.quantity_milli / 1000}</td>
    <td class="num">${inr(l.unit_price_paise)}</td><td class="num">${inr(l.line_total_paise)}</td>
    <td class="num ${l.computed_total_paise !== null && l.computed_total_paise !== l.line_total_paise ? "bad" : ""}">${inr(l.computed_total_paise)}</td></tr>`).join("");
  el.innerHTML = `
    <div class="panel-head detail-head">
      <h2>${esc(d.vendor || "Unknown vendor")} <span class="mono muted">${esc(d.invoice_id)}</span></h2>
      <div class="actions">
        <button class="small" id="backHome">← Today</button>
        <span class="chip ${DECISION_CLASS[d.decision]}">${esc(d.decision_label)}</span>
        <a class="small" href="#" id="openSource">Original document</a>
        <button class="secondary" id="rerun" data-auth>Re-run checks</button>
        ${d.decision === "ELIGIBLE" && !d.proposal ? `<button id="propose" data-auth>Prepare payment</button>` : ""}
        ${d.findings.some((f) => f.code === "VENDOR_UNRESOLVED" && f.status === "OPEN") ? `<button id="approveVendorBtn" data-auth>Approve this vendor…</button>` : ""}
      </div>
    </div>
    ${verdictHtml}
    <div class="grid2">
      <dl class="kv">
        <dt>Invoice no.</dt><dd class="mono">${esc(d.invoice_number || "—")}</dd>
        <dt>Dates</dt><dd>${esc(d.invoice_date || "—")} · due ${esc(d.due_date || "—")}</dd>
        <dt>Stated total</dt><dd>${inr(d.stated_total_paise)}</dd>
        <dt>Computed</dt><dd>${inr(d.computed_total_paise)}</dd>
        <dt>In words</dt><dd>${esc(d.amount_in_words || "—")}</dd>
        <dt>Extraction</dt><dd><span class="chip muted">${esc(d.extraction_source || "none")}</span></dd>
      </dl>
      <dl class="kv">
        <dt>Printed account</dt><dd class="mono">${esc(bank.printed_account || "not printed")} ${esc(bank.printed_ifsc || "")}</dd>
        <dt>Saved account</dt><dd class="mono">${esc(bank.saved_account || "none")} ${esc(bank.saved_ifsc || "")}</dd>
        <dt>Beneficiary</dt><dd class="mono">${esc(bank.saved_beneficiary || "—")}</dd>
        <dt>Payment</dt><dd>${d.attempt ? `<span class="chip ${OVERLAY_CLASS[d.payment_overlay] || "muted"}">${esc(d.attempt.status)}</span> <span class="mono small">${esc(d.attempt.transfer_id)}</span>` : "—"}</dd>
        ${d.attempt && d.attempt.provider_details ? `<dt>Cashfree</dt><dd class="small"><span class="mono">${esc(d.attempt.provider_details.cf_transfer_id || "—")}</span>${d.attempt.provider_details.utr ? ` · UTR <span class="mono">${esc(d.attempt.provider_details.utr)}</span>` : ""} · ${esc([d.attempt.provider_details.status, d.attempt.provider_details.status_code].filter(Boolean).join(" / "))}</dd>` : ""}
      </dl>
    </div>
    ${checksHtml}
    <h3>Findings</h3>${findingsHtml}
    ${callHtml}
    ${d.vendor_id ? `<div id="bankChanges"></div>` : ""}
    ${lines ? `<h3>Line items</h3><div class="table-wrap"><table><thead><tr><th>Item</th><th class="num">Qty</th><th class="num">Unit</th><th class="num">Line total</th><th class="num">Recomputed</th></tr></thead><tbody>${lines}</tbody></table></div>` : ""}
    ${d.notes ? `<h3>Notes on document</h3><div class="note-data"><div class="small">${esc(d.notes_label)}</div>${esc(d.notes)}</div>` : ""}
  `;
  renderSession();
  if (window.innerWidth < 900) el.scrollIntoView({ block: "start" });
  el.querySelectorAll("[data-resolve]").forEach((b) => b.onclick = async () => {
    const reason = await inlineAsk(b, { label: "Why is this finding OK? (recorded in the audit log)",
      placeholder: "e.g. Called the supplier, confirmed account ending 6543", minLength: 3, confirmText: "Mark reviewed" });
    if (!reason) return;
    try {
      const inv = await api(`/api/findings/${b.dataset.resolve}/resolve`, { method: "POST", json: { reason } });
      await refreshBatch();
      await showInvoice(id);
      toast(`Marked reviewed. ${id} is now ${inv.decision}.`);
    } catch (err) { toast(err.message); }
  });
  $("#openSource").onclick = (ev) => { ev.preventDefault(); openSource(id); };
  $("#backHome").onclick = () => {
    stopPolling();
    state.selected = null;
    state.proposalId = null;
    $("#detail").hidden = true;
    $("#approval").hidden = true;
    $("#welcome").hidden = false;
    document.querySelectorAll("#billList .bill").forEach((el) => el.classList.remove("selected"));
    $("#welcome").scrollIntoView({ block: "start" });
  };
  const callBtn = $("#vendorCall");
  if (callBtn) callBtn.onclick = async () => {
    try { await api(`/api/invoices/${id}/vendor-call?${lq()}`, { method: "POST" }); toast("Calling vendor"); showInvoice(id); }
    catch (err) { toast(err.message); }
  };
  // Poll until Relay posts the call result: one timer, given up after 5 minutes per call.
  if (vc && vc.status === "REQUESTED") {
    if (state.callId !== vc.call_id) { state.callId = vc.call_id; state.callDeadline = Date.now() + 5 * 60 * 1000; }
    clearTimeout(state.callTimer);
    if (Date.now() < state.callDeadline) state.callTimer = setTimeout(() => { if (state.selected === id) showInvoice(id); }, 5000);
  }
  $("#rerun").onclick = async (ev) => {
    try {
      const inv = await busy(ev.currentTarget, "Checking…", () => api(`/api/invoices/${id}/checks`, { method: "POST" }));
      await refreshBatch();
      await showInvoice(id);
      const reasons = inv.findings.filter((f) => f.status === "OPEN").map((f) => f.code).join(", ");
      toast(`${id}: ${inv.decision}${reasons ? ` (${reasons})` : ""}`);
    } catch (err) { toast(err.message); }
  };
  const av = $("#approveVendorBtn");
  if (av) av.onclick = () => openVendorDialog(d);
  const propose = $("#propose");
  if (propose) propose.onclick = async () => {
    try {
      const p = await api(`/api/invoices/${id}/proposal?${lq()}`, { method: "POST" });
      renderProposal(p);
    } catch (err) { toast(err.message); }
  };
  if (d.vendor_id) loadBankChanges(d.vendor_id, id, d.findings.some((f) => f.code === "CHANGED_BENEFICIARY" && f.status === "OPEN"));
  if (d.proposal && ["READY_FOR_APPROVAL", "AUTHORIZED", "EXECUTED", "FINISHED"].includes(d.proposal.state)) {
    loadProposal(d.proposal.proposal_id);
  } else {
    clearTimeout(state.pollTimer);  // else the old approval card's poll brings it back
    $("#approval").hidden = true;
    state.proposalId = null;
  }
}

// ---- bank account changes (Secure ID, approved by a person) ---------------------

const BANK_STATE = { PENDING_APPROVAL: ["warn", "Waiting for approval"], VERIFICATION_FAILED: ["bad", "Secure ID check failed"],
  APPROVED: ["ok", "Approved"], REJECTED: ["muted", "Rejected"] };

async function loadBankChanges(vendorId, invoiceId, changed) {
  const el = $("#bankChanges");
  if (!el) return;
  let data;
  try { data = await api(`/api/vendors/${encodeURIComponent(vendorId)}/bank-changes`); } catch (err) { return; }
  const reqs = data.requests.map((r) => {
    const [cls, label] = BANK_STATE[r.state] || ["muted", r.state];
    return `<div class="finding ${r.state === "PENDING_APPROVAL" ? "REVIEW" : r.state === "VERIFICATION_FAILED" ? "BLOCK" : "RESOLVED"}">
      <div class="row"><span><span class="chip ${cls}">${esc(label)}</span>
        <span class="mono">${esc(r.account)} ${esc(r.ifsc)}</span> · ${esc(r.account_holder_name)}</span>
        ${r.approvable ? `<span><button class="small" data-bc-approve="${esc(r.request_id)}" data-auth>Approve new account</button>
          <button class="secondary small" data-bc-reject="${esc(r.request_id)}" data-auth>Reject</button></span>` : ""}</div>
      <p class="small">Secure ID: account ${esc(r.account_status || "—")}, name match ${esc(r.name_match_result || "—")}${r.name_at_bank ? ` (bank has “${esc(r.name_at_bank)}”)` : ""}.
        Confirmed by ${esc(r.requested_by)}: ${esc(r.confirmation_note)}${r.decided_reason ? ` · Rejected: ${esc(r.decided_reason)}` : ""}</p>
    </div>`;
  }).join("");
  const form = data.secure_id_live ? `
    <details ${changed && !data.requests.some((r) => r.approvable) ? "open" : ""}><summary>Change bank details</summary>
      <p class="small muted">Only after confirming the new account with the firm by phone or in person. Do not copy it from the invoice alone.
        Cashfree Secure ID checks the name on the account, then a person approves it.</p>
      <form id="bankChangeForm" class="bank-form">
        <input name="account_number" placeholder="New account number" inputmode="numeric" autocomplete="off" required>
        <input name="ifsc" placeholder="IFSC" maxlength="11" autocomplete="off" required>
        <input name="account_holder_name" placeholder="Account holder name (blank = saved name)" autocomplete="off">
        <input name="confirmation_note" placeholder="How you confirmed it (who, when, which number)" required minlength="5">
        <button data-auth>Check with Secure ID</button>
      </form>
    </details>` : `<p class="small muted">Turn on Cashfree Secure ID to change a vendor's bank details from here.</p>`;
  el.innerHTML = `<h3>Bank account changes</h3>${reqs}${form}`;
  renderSession();
  const f = $("#bankChangeForm");
  if (f) f.onsubmit = async (e) => {
    e.preventDefault();
    const json = Object.fromEntries(new FormData(f));
    try {
      const r = await api(`/api/vendors/${encodeURIComponent(vendorId)}/bank-changes`, { method: "POST", json });
      toast(r.approvable ? "Name matches. Review and approve the new account." : `Secure ID: ${r.account_status}, ${r.name_match_result}. Cannot be approved.`);
      loadBankChanges(vendorId, invoiceId, changed);
    } catch (err) { toast(err.message); }
  };
  el.querySelectorAll("[data-bc-approve]").forEach((b) => b.onclick = async () => {
    const ok = await inlineAsk(b, { label: "Make this the vendor's saved bank account? Invoices with the old account will be blocked.",
      needsText: false, confirmText: "Yes, approve new account" });
    if (!ok) return;
    try { await api(`/api/bank-changes/${b.dataset.bcApprove}/approve`, { method: "POST" }); toast("Saved account updated"); await refreshBatch(); showInvoice(invoiceId); }
    catch (err) { toast(err.message); }
  });
  el.querySelectorAll("[data-bc-reject]").forEach((b) => b.onclick = async () => {
    const reason = await inlineAsk(b, { label: "Why is this change rejected? (recorded in the audit log)",
      placeholder: "e.g. Supplier denied changing their account", minLength: 3, confirmText: "Reject change" });
    if (!reason) return;
    try { await api(`/api/bank-changes/${b.dataset.bcReject}/reject`, { method: "POST", json: { reason } }); loadBankChanges(vendorId, invoiceId, changed); }
    catch (err) { toast(err.message); }
  });
}

// The source route needs the session header, which a plain link cannot send.
async function openSource(id) {
  const win = window.open("", "_blank");  // opened inside the click so popup blockers allow it
  try {
    const res = await fetch(`/api/invoices/${encodeURIComponent(id)}/source`, { headers: { Authorization: `Bearer ${state.token}` } });
    if (res.status === 401) signOut();
    if (!res.ok) throw new Error(res.status === 401 ? "Sign in again to open the document" : "Original document is not available");
    const url = URL.createObjectURL(await res.blob());
    if (win) win.location.href = url; else window.open(url, "_blank");
    setTimeout(() => URL.revokeObjectURL(url), 60000);
  } catch (err) { if (win) win.close(); toast(err.message); }
}

// Transaction details exactly as Cashfree returned them (account numbers masked server-side).
function cashfreeDetails(a) {
  const x = a && a.provider_details;
  if (!x) return "";
  const when = (s) => (s ? new Date(s.replace(" ", "T")).toLocaleString() : "—");
  const row = (k, v, mono) => (v === null || v === undefined || v === "" ? "" : `<dt>${esc(k)}</dt><dd${mono ? ' class="mono"' : ""}>${esc(v)}</dd>`);
  return `
    <h3>Cashfree transaction</h3>
    <dl class="kv small cf-details">
      ${row("Cashfree reference", x.cf_transfer_id, true)}
      ${row("Transfer ID", x.transfer_id, true)}
      ${row("Status", [x.status, x.status_code].filter(Boolean).join(" · "))}
      ${row("Bank reference (UTR)", x.utr, true)}
      ${row("Amount", x.transfer_amount !== undefined && x.transfer_amount !== null ? `₹${Number(x.transfer_amount).toLocaleString("en-IN", { minimumFractionDigits: 2 })}${x.currency ? " " + x.currency : ""}` : "")}
      ${row("Mode", x.transfer_mode ? String(x.transfer_mode).toUpperCase() : "")}
      ${row("Service charge", x.service_charge)}${row("Service tax", x.service_tax)}
      ${row("Beneficiary", [x.beneficiary_account, x.beneficiary_ifsc, x.beneficiary_vpa].filter(Boolean).join(" · "), true)}
      ${row("Cashfree beneficiary ID", x.beneficiary_id, true)}
      ${row("Fund source", x.fundsource_id, true)}
      ${row("Created at Cashfree", x.added_on ? when(x.added_on) : "")}
      ${row("Last updated at Cashfree", x.updated_on ? when(x.updated_on) : "")}
      ${row("Last checked by Nipta", a.last_checked_at ? new Date(a.last_checked_at * 1000).toLocaleString() : "")}
    </dl>
    ${x.status_description ? `<p class="small cf-desc">${esc(x.status_description)}</p>` : ""}
    <details><summary>All fields from Cashfree</summary><pre>${esc(JSON.stringify(x.raw, null, 2))}</pre></details>`;
}

// ---- approval card --------------------------------------------------------------

async function loadProposal(id) {
  try { renderProposal(await api(`/api/proposals/${id}/status?${lq()}`)); }
  catch (err) { toast(err.message); }
}

const MODE_LABEL = {
  CASHFREE_SANDBOX: ["info", "Cashfree SANDBOX payout"],
  SIMULATED: ["muted", "SIMULATED: no Cashfree transfer will be made"],
  TEST_PROVIDER: ["warn", "TEST PROVIDER: labelled test events, no money moves"],
};
const TERMINAL = new Set(["COMPLETED", "FAILED", "REVERSED", "SIMULATED"]);

function renderProposal(p) {
  state.proposalId = p.proposal_id;
  const el = $("#approval");
  el.hidden = false;
  const [modeCls, modeText] = MODE_LABEL[p.dispatch_mode] || ["muted", p.dispatch_mode];
  const a = p.attempt;
  const timeline = (p.transitions || []).map((t) => `
    <li class="${t.applied ? "" : "ignored"}"><span><strong>${esc(t.to_status)}</strong>
      <span class="small muted">${esc(t.source)}${t.provider_status ? ` · ${esc(t.provider_status)}${t.provider_status_code ? "/" + esc(t.provider_status_code) : ""}` : ""}
      ${t.applied ? "" : " · ignored (would move backwards)"} ${t.note ? "· " + esc(t.note) : ""} · ${new Date(t.created_at * 1000).toLocaleTimeString()}</span></span></li>`).join("");
  el.innerHTML = `
    <div class="panel-head">
      <h2>Approve this payment</h2>
      <span class="chip ${modeCls}">${esc(modeText)}</span>
    </div>
    <div class="pay-focus">
      <div class="small muted" style="font-weight:700">Pay</div>
      <div class="payee">${esc(p.vendor)}</div>
      <div class="amount">${esc(p.amount_display)}</div>
      <div>to saved account <strong class="mono">${esc(p.beneficiary_display)}</strong> · invoice ${esc(p.invoice_number)}</div>
      <div class="warn-row"><span class="chip ${p.verification_label.startsWith("Live") ? "ok" : "warn"}">${esc(p.verification_label)}</span></div>
    </div>
    <details class="audit"><summary>Audit details</summary>
      <dl class="kv small">
        <dt>Proposal</dt><dd class="mono">${esc(p.proposal_id)}</dd>
        <dt>State</dt><dd><strong>${esc(p.state)}</strong></dd>
        <dt>Snapshot hash</dt><dd class="mono">${esc(p.snapshot_hash)}</dd>
        <dt>Expires</dt><dd>${new Date(p.expires_at * 1000).toLocaleTimeString()}</dd>
        ${p.approver_id ? `<dt>Approved by</dt><dd>${esc(p.approver_id)} at ${new Date(p.approved_at * 1000).toLocaleTimeString()}</dd>` : ""}
        ${a ? `<dt>Transfer ID</dt><dd class="mono">${esc(a.transfer_id)}</dd>` : ""}
        ${a && a.cf_transfer_id ? `<dt>Provider ref</dt><dd class="mono">${esc(a.cf_transfer_id)}</dd>` : ""}
        <dt>Ledger posted</dt><dd>${p.ledger_posted ? "Yes (one settlement)" : "No"}</dd>
      </dl>
    </details>
    <p><strong>${esc(p.status_text)}</strong></p>
    <div class="btn-row">
      ${p.state === "READY_FOR_APPROVAL" ? `<button id="approveBtn" data-auth>Approve ${esc(p.amount_display)}</button>` : ""}
      ${p.state === "AUTHORIZED" && !a ? `<button id="executeBtn" data-auth>Execute approved payout</button>` : ""}
      ${a && a.status === "REQUEST_RESERVED" && !a.dispatch_started_at && p.dispatch_mode === "CASHFREE_SANDBOX" ? `<button id="executeBtn" data-auth>Send reserved payout (same transfer ID)</button>` : ""}
      ${a && !TERMINAL.has(a.status) ? `<button id="refreshStatus" class="secondary">Check status</button>` : ""}
      <button id="speak" class="secondary" data-auth>Speak status</button>
    </div>
    ${cashfreeDetails(a)}
    ${timeline ? `<h3>Status timeline</h3><ol class="timeline">${timeline}</ol>` : ""}
    ${p.postings && p.postings.length ? `<h3>Postings</h3><ul>${p.postings.map((x) => `<li>${esc(x.kind)} ${inr(x.amount_paise)}</li>`).join("")}</ul>` : ""}
  `;
  renderSession();
  el.scrollIntoView({ block: "start", behavior: "smooth" });
  const approveBtn = $("#approveBtn");
  if (approveBtn) approveBtn.onclick = async () => {
    try {
      renderProposal(await api(`/api/proposals/${p.proposal_id}/approve?${lq()}`, { method: "POST", json: { snapshot_hash: p.snapshot_hash } }));
      toast("Approved. Nothing has been sent yet.");
    } catch (err) { toast(err.message); refreshBatch(); }
  };
  const execBtn = $("#executeBtn");
  if (execBtn) execBtn.onclick = async () => {
    execBtn.disabled = true;
    try {
      renderProposal(await api(`/api/proposals/${p.proposal_id}/execute?${lq()}`, { method: "POST" }));
      refreshBatch();
    } catch (err) { toast(err.message); refreshBatch(); }
  };
  const rs = $("#refreshStatus");
  if (rs) rs.onclick = () => loadProposal(p.proposal_id);
  $("#speak").onclick = () => speak("proposal", p.proposal_id);
  clearTimeout(state.pollTimer);
  if (a && !TERMINAL.has(a.status)) state.pollTimer = setTimeout(() => { if (state.proposalId === p.proposal_id) loadProposal(p.proposal_id); }, 4000);
}

async function speak(kind, id) {
  try {
    const res = await fetch("/api/speech/tts", {
      method: "POST",
      headers: { "Content-Type": "application/json", Authorization: `Bearer ${state.token}` },
      body: JSON.stringify({ kind, object_id: id, language: state.lang }),
    });
    if (!res.ok) { const b = await res.json(); throw new Error(`${b.error}: ${b.message}`); }
    const blob = await res.blob();
    new Audio(URL.createObjectURL(blob)).play();
  } catch (err) { toast(err.message); }
}

// ---- agent ----------------------------------------------------------------------

$("#agentForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  const text = $("#agentText").value.trim();
  if (!text || !state.batchId) return;
  const out = $("#agentReply");
  out.textContent = "…";
  try {
    renderAgentReply(await api("/api/agent/message", { method: "POST", json: { batch_id: state.batchId, text, language: state.lang } }));
  } catch (err) { out.textContent = err.message; }
});

function renderAgentReply(r) {
  const out = $("#agentReply");
  out.innerHTML =
    (r.transcript !== undefined ? `<div class="heard">“${esc(r.transcript)}”${r.language_code ? ` · ${esc(r.language_code)}` : ""}</div>` : "") +
    `${esc(r.reply)}<div class="calls">${esc(r.router || "")}${r.router_note ? " · " + esc(r.router_note) : ""} · ${r.tool_calls.map((c) => `${esc(c.tool)}(${esc(c.invoice_id || "")}) ${esc(c.outcome)}`).join(", ") || "no tools"}</div>`;
  if (r.reply_audio_b64) {
    const bytes = Uint8Array.from(atob(r.reply_audio_b64), (ch) => ch.charCodeAt(0));
    new Audio(URL.createObjectURL(new Blob([bytes], { type: "audio/wav" }))).play().catch(() => {});
  }
  if (r.proposals && r.proposals.length) {
    refreshBatch().then(() => showInvoice(r.proposals[0].invoice_id));
  } else loadActivity();
}

// ---- voice questions (Sarvam speech-to-text) ----------------------------------

const voice = { rec: null, chunks: [], timer: null };

async function startRecording() {
  if (!navigator.mediaDevices || !window.MediaRecorder) { toast("This browser cannot record audio"); return; }
  let stream;
  try { stream = await navigator.mediaDevices.getUserMedia({ audio: true }); }
  catch (_) { toast("Microphone permission denied"); return; }
  const type = ["audio/webm;codecs=opus", "audio/webm", "audio/ogg;codecs=opus", "audio/mp4"].find((t) => MediaRecorder.isTypeSupported(t)) || "";
  voice.rec = new MediaRecorder(stream, type ? { mimeType: type } : undefined);
  voice.chunks = [];
  voice.rec.ondataavailable = (e) => e.data.size && voice.chunks.push(e.data);
  voice.rec.onstop = async () => {
    stream.getTracks().forEach((t) => t.stop());
    $("#micBtn").classList.remove("recording");
    clearTimeout(voice.timer);
    const blob = new Blob(voice.chunks, { type: voice.rec.mimeType || "audio/webm" });
    voice.rec = null;
    if (!blob.size) return;
    const ext = blob.type.includes("ogg") ? "ogg" : blob.type.includes("mp4") ? "m4a" : "webm";
    const fd = new FormData();
    fd.append("audio", blob, `question.${ext}`);
    fd.append("batch_id", state.batchId);
    fd.append("language", "auto");
    $("#agentReply").textContent = "Listening…";
    try { renderAgentReply(await api("/api/agent/voice", { method: "POST", body: fd })); }
    catch (err) { $("#agentReply").textContent = err.message; }
  };
  voice.rec.start();
  $("#micBtn").classList.add("recording");
  voice.timer = setTimeout(() => voice.rec && voice.rec.stop(), 25000);
}

$("#micBtn").addEventListener("click", () => {
  if (!state.batchId) return;
  if (voice.rec) voice.rec.stop(); else startRecording();
});

// ---- vendor approval ----------------------------------------------------------

async function loadVendors() {
  const list = await api("/api/vendors");
  $("#vendorList").innerHTML = list.map((v) => `<li><span>${esc(v.display_name)}<br><span class="small muted">${esc(v.aliases.filter((a) => a !== v.display_name).join(" · "))}</span></span>
    <span class="small">${v.beneficiary ? `<span class="mono">${esc(v.beneficiary.account)}</span> ${esc(v.beneficiary.ifsc)}` : "no account"}<br>
    <span class="chip ${v.verification ? (v.verification.source === "LIVE_SECURE_ID_BAV" ? "ok" : "muted") : "warn"}">${v.verification ? esc(v.verification.source === "LIVE_SECURE_ID_BAV" ? "verified" : "seeded mock") : "not verified"}</span></span></li>`).join("");
}

async function openVendorDialog(inv) {
  const f = $("#vendorForm");
  f.reset();
  $("#vendorError").textContent = "";
  const hint = $("#vendorFromInvoice");
  if (inv) {
    f.source_invoice_id.value = inv.invoice_id;
    let draft = null;
    try { draft = await api(`/api/invoices/${encodeURIComponent(inv.invoice_id)}/vendor-draft`); }
    catch (err) { toast(err.message); }
    f.display_name.value = (draft && draft.display_name) || inv.vendor_name_raw || "";
    f.account_holder_name.value = (draft && draft.account_holder_name) || inv.vendor_name_raw || "";
    if (draft && draft.bank_account_number) {
      f.bank_account_number.value = draft.bank_account_number;
      f.bank_account_number_confirm.value = draft.bank_account_number;
    }
    if (draft && draft.ifsc) f.ifsc.value = draft.ifsc;
    hint.hidden = false;
    hint.innerHTML = `Filled from what <span class="mono">${esc(inv.invoice_id)}</span> prints.
      ${draft && draft.account_is_masked_on_invoice ? `The invoice only shows <span class="mono">${esc(draft.printed_account_masked)}</span>, so type the full account. ` : ""}
      Printed details are a claim: confirm the account with the supplier before approving. The new account stays
      unverified until Secure ID verifies it or you mark <span class="mono">VERIFICATION_MISSING</span> reviewed with a reason.`;
  } else {
    f.source_invoice_id.value = "";
    hint.hidden = true;
  }
  $("#vendorDialog").showModal();
}

$("#addVendor").addEventListener("click", () => openVendorDialog(null));
$("#vendorCancel").addEventListener("click", () => $("#vendorDialog").close());
$("#vendorForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  const f = e.target;
  const body = {
    display_name: f.display_name.value.trim(),
    aliases: f.aliases.value.split(",").map((s) => s.trim()).filter(Boolean),
    account_holder_name: f.account_holder_name.value.trim(),
    bank_account_number: f.bank_account_number.value,
    bank_account_number_confirm: f.bank_account_number_confirm.value,
    ifsc: f.ifsc.value.trim().toUpperCase(),
    payment_terms_days: f.payment_terms_days.value ? Number(f.payment_terms_days.value) : null,
    source_invoice_id: f.source_invoice_id.value || null,
  };
  if (body.bank_account_number !== body.bank_account_number_confirm) {
    $("#vendorError").textContent = "The two account numbers do not match.";
    return;
  }
  try {
    const r = await busy($("#vendorSubmit"), "Approving…", () => api("/api/vendors", { method: "POST", json: body }));
    $("#vendorDialog").close();
    await loadVendors();
    await refreshBatch();
    const re = r.rechecked_invoices.map((x) => `${x.invoice_id}: ${x.decision}`).join(", ");
    toast(`Approved ${r.vendor.display_name} (${r.vendor.beneficiary.account}). ${r.verification === "LIVE_SECURE_ID_BAV" ? "Verified with Secure ID." : "Not verified yet."}${re ? " Re-checked " + re : ""}`);
    if (body.source_invoice_id) showInvoice(body.source_invoice_id);
    if (r.matches_invoice_print === false) toast("Warning: the account you typed differs from the one printed on the invoice; it will be blocked.");
  } catch (err) { $("#vendorError").textContent = err.message; }
});

// ---- payment plan -------------------------------------------------------------

$("#planForm").addEventListener("submit", (e) => { e.preventDefault(); loadPlan(); });

async function loadPlan() {
  if (!state.batchId) return;
  const budget = Math.round(Number($("#budget").value || 0) * 100);
  const plan = await api(`/api/payment-plan?batch_id=${state.batchId}&cash_budget_paise=${budget}`);
  const item = (e, right) => `<li><span><span class="mono">${esc(e.invoice_id)}</span> ${esc(e.vendor || "")}${e.deadline_note ? `<br><span class="small muted">${esc(e.deadline_note)}</span>` : ""}</span><span class="small">${right}</span></li>`;
  $("#plan").innerHTML = `
    <h3>Pay now</h3><ul class="plan-list">${plan.selected.map((e) => item(e, `#${e.rank} · ${inr(e.amount_paise)}${e.overdue_days ? ` · ${e.overdue_days}d overdue` : ""}`)).join("") || `<li class="muted">Nothing fits</li>`}</ul>
    ${plan.skipped.length ? `<h3>Skipped</h3><ul class="plan-list">${plan.skipped.map((e) => item(e, `${inr(e.amount_paise)} · exceeds remaining ${inr(e.remaining_budget_paise)}`)).join("")}</ul>` : ""}
    ${plan.excluded.length ? `<details><summary>Not in the plan (${plan.excluded.length})</summary><ul class="plan-list">${plan.excluded.map((e) => item(e, esc(e.reason.replace(/_/g, " ").toLowerCase()))).join("")}</ul></details>` : ""}
    <p class="small muted">Remaining ${inr(plan.remaining_paise)}. ${esc(plan.disclaimer)}</p>
    <div class="btn-row">
      ${plan.selected.length ? `<button class="small" data-bulk="plan" data-auth>Prepare plan payments (${plan.selected.length} · ${inr(plan.selected.reduce((t, e) => t + e.amount_paise, 0))})</button>` : ""}
      ${(() => {
        const ready = [...plan.selected, ...plan.skipped];  // every safe bill not yet sent
        return ready.length ? `<button class="secondary small" data-bulk="all_ready" data-auth>Prepare all ready (${ready.length} · ${inr(ready.reduce((t, e) => t + e.amount_paise, 0))})</button>` : "";
      })()}
    </div>
    ${bulkButtons() ? `<div class="btn-row">${bulkButtons()}</div>` : ""}
    ${plan.skipped.length ? `<p class="small">Left out by the budget: ${plan.skipped.map((e) => `${esc(e.invoice_id)} (${inr(e.amount_paise)})`).join(", ")}.
      Raise the budget to ${inr(plan.cash_budget_paise - plan.remaining_paise + plan.skipped.reduce((t, e) => t + e.amount_paise, 0))} to include ${plan.skipped.length === 1 ? "it" : "them"}, or use "Prepare all ready".</p>` : ""}
    <p class="small muted">Preparing only creates proposals. Each payment is still approved and sent one by one.</p>`;
  state.plan = plan;
  renderToday(plan);
  bindBulk($("#plan"));
  bindBulkReview($("#plan"));
}

// ---- bulk approve and send (each proposal still checked on its own snapshot) -----

function queueCounts() {
  const b = state.batch;
  if (!b) return { toApprove: 0, toSend: 0 };
  const open = b.invoices.filter((i) => i.proposal && !i.payment_overlay);
  return { toApprove: open.filter((i) => i.proposal.state === "READY_FOR_APPROVAL").length,
           toSend: open.filter((i) => i.proposal.state === "AUTHORIZED").length };
}

function bulkButtons() {
  const q = queueCounts();
  return (q.toApprove ? `<button class="small primary" data-bulkreview="approve" data-auth>Review &amp; approve ${q.toApprove}</button>` : "")
    + (q.toSend ? `<button class="small" data-bulkreview="send" data-auth>Send ${q.toSend} approved</button>` : "");
}

function bindBulkReview(root) {
  if (!root) return;
  root.querySelectorAll("[data-bulkreview]").forEach((b) => b.onclick = () => openBulkReview(b.dataset.bulkreview));
  renderSession();
}

async function openBulkReview(mode) {
  let q;
  try { q = await api(`/api/batches/${state.batchId}/approval-queue?${lq()}`); } catch (err) { toast(err.message); return; }
  const items = mode === "approve" ? q.to_approve : q.to_send;
  if (!items.length) { toast(mode === "approve" ? "Nothing waiting for approval." : "Nothing approved to send."); return; }
  const MODE = { CASHFREE_SANDBOX: "Cashfree sandbox", SIMULATED: "Simulated", TEST_PROVIDER: "Test provider" };
  $("#bulkTitle").textContent = mode === "approve" ? "Review and approve payments" : "Send approved payments";
  $("#bulkIntro").textContent = mode === "approve"
    ? "Check each payee and amount. Untick anything you don't want to approve. Each payment is approved on its own frozen snapshot; nothing is sent yet."
    : "Each selected payment is sent once to its saved account, with its own fixed transfer ID.";
  $("#bulkHead").innerHTML = `<tr><th></th><th>Vendor</th><th>Bill</th><th>To account</th><th class="num">Amount</th><th>How</th><th>Check</th></tr>`;
  $("#bulkRows").innerHTML = items.map((i) => `<tr data-pid="${esc(i.proposal_id)}">
      <td><input type="checkbox" class="bulk-pick" ${i.expired ? "disabled" : "checked"} data-amount="${i.amount_paise}" data-hash="${esc(i.snapshot_hash)}"></td>
      <td>${esc(i.vendor)}</td><td class="mono small">${esc(i.invoice_number || i.invoice_id)}</td>
      <td class="mono">${esc(i.beneficiary_display)}</td><td class="num">${esc(i.amount_display)}</td>
      <td class="small">${esc(MODE[i.dispatch_mode] || i.dispatch_mode)}</td>
      <td class="small ${i.expired ? "bad-text" : "muted"}">${i.expired ? "Expired: prepare again" : esc(i.verification_label)}</td></tr>`).join("");
  const picked = () => [...document.querySelectorAll("#bulkRows .bulk-pick:checked")];
  const total = () => picked().reduce((t, c) => t + Number(c.dataset.amount), 0);
  const refreshTotal = () => {
    $("#bulkTotal").innerHTML = `<b>${picked().length}</b> selected · total <b>${inr(total())}</b>`;
    const go = $("#bulkGo"); if (go) { go.disabled = !picked().length; go.textContent = mode === "approve" ? `Approve ${picked().length} (${inr(total())})` : `Send ${picked().length} (${inr(total())})`; }
  };
  $("#bulkMsg").innerHTML = "";
  $("#bulkActions").innerHTML = `<button id="bulkGo" class="primary">Approve</button><button class="secondary" id="bulkClose">Close</button>`;
  document.querySelectorAll("#bulkRows .bulk-pick").forEach((c) => c.onchange = refreshTotal);
  refreshTotal();
  $("#bulkClose").onclick = () => $("#bulkDialog").close();
  $("#bulkGo").onclick = async () => {
    const sel = picked();
    if (!sel.length) return;
    try {
      if (mode === "approve") {
        const items = sel.map((c) => ({ proposal_id: c.closest("tr").dataset.pid, snapshot_hash: c.dataset.hash }));
        const r = await busy($("#bulkGo"), "Approving…", () => api(`/api/batches/${state.batchId}/approve-bulk`,
          { method: "POST", json: { items, confirm_total_paise: total() } }));
        markRows(r.results, (x) => (x.outcome === "AUTHORIZED" ? "Approved" : `${x.outcome}${x.reason ? ": " + x.reason : ""}`));
        await refreshBatch();
        const sendable = r.results.filter((x) => x.outcome === "AUTHORIZED").map((x) => x.proposal_id);
        $("#bulkMsg").textContent = `Approved ${r.approved} of ${sel.length}. Nothing has been sent yet.`;
        $("#bulkActions").innerHTML = (sendable.length ? `<button id="bulkSendNow" class="primary">Send ${sendable.length} approved now</button>` : "")
          + `<button class="secondary" id="bulkClose2">Close</button>`;
        $("#bulkClose2").onclick = () => $("#bulkDialog").close();
        if (sendable.length) $("#bulkSendNow").onclick = () => sendBulk(sendable);
      } else {
        await sendBulk(sel.map((c) => c.closest("tr").dataset.pid));
      }
    } catch (err) { $("#bulkMsg").innerHTML = `<span class="bad-text">${esc(err.message)}</span>`; }
  };
  $("#bulkDialog").showModal();
}

function markRows(results, label) {
  results.forEach((x) => {
    const tr = document.querySelector(`#bulkRows tr[data-pid="${CSS.escape(x.proposal_id)}"]`);
    if (!tr) return;
    const c = tr.querySelector(".bulk-pick"); if (c) { c.checked = false; c.disabled = true; }
    tr.lastElementChild.innerHTML = `<b>${esc(label(x))}</b>`;
  });
}

async function sendBulk(ids) {
  const btn = $("#bulkSendNow") || $("#bulkGo");
  const r = await busy(btn, "Sending…", () => api(`/api/batches/${state.batchId}/execute-bulk?${lq()}`, { method: "POST", json: { proposal_ids: ids } }));
  markRows(r.results, (x) => (x.transfer_id ? `${x.outcome} · ${x.transfer_id}` : `${x.outcome}${x.reason ? ": " + x.reason : ""}`));
  const sent = r.results.filter((x) => x.transfer_id).length;
  $("#bulkMsg").textContent = `Sent ${sent} of ${ids.length}. Status updates as Cashfree reports it; a settlement is recorded only on confirmed completion.`;
  $("#bulkActions").innerHTML = `<button class="secondary" id="bulkClose3">Close</button>`;
  $("#bulkClose3").onclick = () => $("#bulkDialog").close();
  await refreshBatch();
}

// ---- bulk preparation (proposals only; approval stays one by one) ---------------

function bindBulk(root) {
  if (!root) return;
  renderSession();
  root.querySelectorAll("[data-bulk]").forEach((b) => b.onclick = async () => {
    const budget = Math.round(Number($("#budget").value || 0) * 100);
    try {
      const r = await busy(b, "Preparing…", () => api(`/api/batches/${state.batchId}/proposals`, {
        method: "POST", json: { scope: b.dataset.bulk, cash_budget_paise: budget } }));
      await refreshBatch();
      const parts = [];
      if (r.created.length) parts.push(`Prepared ${r.created.length} (${inr(r.total_paise)})`);
      if (r.already_prepared.length) parts.push(`${r.already_prepared.length} already prepared`);
      if (r.skipped.length) parts.push(`${r.skipped.length} skipped: ${r.skipped.map((s) => `${s.invoice_id} ${s.code}`).join(", ")}`);
      toast((parts.join(" · ") || "Nothing to prepare") + ". Approve each one from Today.");
    } catch (err) { toast(err.message); }
  });
}

// Home: what needs Raghu today, in order: pay (plan order), don't pay, check, wait, done.
function renderToday(plan) {
  const b = state.batch;
  if (!b || !b.invoices.length) return;
  const inv = Object.fromEntries(b.invoices.map((i) => [i.invoice_id, i]));
  const reason = (i) => ((i.findings || []).find((f) => f.status === "OPEN") || {}).explanation || "";
  const row = (cls, title, sub, amt, id, action) => `
    <li class="todo"><span class="dot ${cls}"></span>
      <span class="what"><b>${esc(title)}</b><span>${esc(sub)}</span></span>
      <span class="amt">${inr(amt)}</span>
      ${action ? `<button class="small ${cls === "ok" ? "primary" : ""}" data-open="${esc(id)}">${esc(action)}</button>` : "<span></span>"}</li>`;
  const items = [
    ...plan.selected.map((e) => {
      const ps = (inv[e.invoice_id] && inv[e.invoice_id].proposal && inv[e.invoice_id].proposal.state) || "";
      const stage = ps === "READY_FOR_APPROVAL" ? " · ready to approve" : ps === "AUTHORIZED" ? " · approved, ready to send" : "";
      const action = ps === "READY_FOR_APPROVAL" ? "Approve" : ps === "AUTHORIZED" ? "Send" : "Review & pay";
      return row("ok", `Pay ${e.vendor}`, `#${e.rank} in this Friday's plan · due ${e.due_date || "—"}${e.overdue_days ? ` · ${e.overdue_days}d overdue` : ""}${stage}`, e.amount_paise, e.invoice_id, action);
    }),
    ...b.invoices.filter((i) => i.decision === "BLOCKED").map((i) => row("bad", `Don't pay ${i.vendor || i.invoice_id}`, reason(i), i.stated_total_paise, i.invoice_id,
      i.findings.some((f) => f.code === "CHANGED_BENEFICIARY" && f.status === "OPEN") ? "Call vendor" : "See why")),
    ...b.invoices.filter((i) => i.decision === "REVIEW").map((i) => row("warn", `Check ${i.vendor || i.invoice_id}`, reason(i), i.stated_total_paise, i.invoice_id, "Check")),
    ...plan.skipped.map((e) => row("muted", `Wait: ${e.vendor}`, "Not enough cash left this Friday", e.amount_paise, e.invoice_id, "Open")),
    ...b.invoices.filter((i) => i.payment_overlay).map((i) => row("muted", `Sent: ${i.vendor}`, i.payment_overlay.replace(/_/g, " ").toLowerCase(), i.stated_total_paise, i.invoice_id, "Status")),
  ];
  const n = (k) => b.invoices.filter((i) => i.decision === k).length;
  const payNow = plan.selected.reduce((t, e) => t + e.amount_paise, 0);
  $("#today").innerHTML = `
    <h2 class="hero">Today · ${b.invoices.length} bills</h2>
    <p>${plan.selected.length} to pay now (${inr(payNow)}), ${n("REVIEW")} to check, ${n("BLOCKED")} blocked${b.invoices.filter((i) => ["PAID", "PAYMENT_PENDING", "PAYMENT_UNKNOWN", "SIMULATED"].includes(i.payment_overlay)).length ? `, ${b.invoices.filter((i) => ["PAID", "PAYMENT_PENDING", "PAYMENT_UNKNOWN", "SIMULATED"].includes(i.payment_overlay)).length} paid or sent` : ""}. Cash left after the plan: ${inr(plan.remaining_paise)}.</p>
    ${plan.selected.some((e) => !(inv[e.invoice_id] && inv[e.invoice_id].proposal && ["READY_FOR_APPROVAL", "AUTHORIZED"].includes(inv[e.invoice_id].proposal.state)))
      ? `<div class="btn-row"><button class="primary small" data-bulk="plan" data-auth>Prepare ${plan.selected.length} payment${plan.selected.length === 1 ? "" : "s"} (${inr(payNow)})</button></div>` : ""}
    ${bulkButtons() ? `<div class="btn-row">${bulkButtons()}</div>` : ""}
    <ol class="todo-list">${items.join("")}</ol>`;
  document.querySelectorAll("#today [data-open]").forEach((btn) => btn.onclick = () => showInvoice(btn.dataset.open));
  bindBulk($("#today"));
  bindBulkReview($("#today"));
}

// ---- activity -------------------------------------------------------------------

function describe(e) {
  const d = e.detail || {};
  switch (e.action) {
    case "PIPELINE_STEP": return `${d.invoice_id || ""}: ${d.summary || d.step}${d.reason_codes && d.reason_codes.length ? " → " + d.reason_codes.join(", ") : ""}`;
    case "AGENT_TOOL": return `Agent ${d.tool}(${d.invoice_id || ""}) ${d.outcome}${d.reason_codes && d.reason_codes.length ? " · " + d.reason_codes.join(", ") : ""}`;
    case "AGENT_CAP_REACHED": return `Agent stopped at ${d.cap} tool calls`;
    case "PROPOSAL_CREATED": return `Proposal for ${d.invoice_id}: ${inr(d.amount_paise)} (${d.dispatch_mode})`;
    case "PROPOSAL_APPROVED": return `Approved ${d.invoice_id}, hash ${String(d.snapshot_hash).slice(0, 12)}…`;
    case "PROPOSAL_STALE": return `Approval stale: ${(d.changed_fields || []).join(", ")}`;
    case "PAYMENT_RESERVED": return `Reserved ${d.transfer_id} (${inr(d.amount_paise)})`;
    case "PAYMENT_SIMULATED": return `Simulated payment ${inr(d.amount_paise)}: no Cashfree transfer`;
    case "ATTEMPT_TRANSITION": return `${d.transfer_id}: ${d.from} → ${d.to} (${d.source})`;
    case "TRANSITION_IGNORED": return `Ignored ${d.from} → ${d.to} (${d.source})`;
    case "SETTLEMENT_POSTED": return `Settlement posted ${inr(d.amount_paise)} for ${d.invoice_id}`;
    case "REVERSAL_POSTED": return `Reversal posted for ${d.invoice_id}`;
    case "FINDING_RESOLVED": return `${d.invoice_id}: ${d.code} resolved (“${d.reason}”)`;
    case "WEBHOOK_REJECTED": return `Webhook rejected: ${d.reason}`;
    case "BENEFICIARY_VERIFIED": return `Secure ID ${d.account}: ${d.account_status}, ${d.name_match_result}`;
    case "BENEFICIARY_VERIFICATION_ERROR": return `Secure ID check failed for ${e.object_id}: ${d.error}`;
    case "BANK_CHANGE_REQUESTED": return `Bank change ${d.from} → ${d.to} ${d.ifsc}: ${d.account_status}, ${d.name_match_result}`;
    case "BANK_CHANGE_REJECTED": return `Bank change rejected (“${d.reason}”)`;
    case "BENEFICIARY_CHANGED": return `Saved account changed ${d.from} → ${d.to} (v${d.from_version} → v${d.to_version})`;
    default: return `${e.action.replace(/_/g, " ").toLowerCase()} ${e.object_id || ""}`;
  }
}

async function loadActivity() {
  if (!state.batchId) return;
  const events = await api(`/api/audit?batch_id=${state.batchId}&limit=80`);
  $("#activity").innerHTML = events.map((e) =>
    `<li><time>${new Date(e.created_at * 1000).toLocaleTimeString()}</time><span class="actor">${esc(e.actor)}</span>${esc(describe(e))}</li>`
  ).join("");
}
$("#refreshAudit").addEventListener("click", loadActivity);

// ---- boot -----------------------------------------------------------------------

renderSession();
loadHealth().catch((e) => toast(e.message));  // public: capability badges only
if (state.token) {
  loadBatches().catch((e) => toast(e.message));
  loadVendors().catch((e) => toast(e.message));
} else renderSignedOut();
