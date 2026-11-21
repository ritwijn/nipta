"use strict";

const state = {
  token: null,
  operator: null,
  batchId: null,
  batch: null,
  selected: null,
  proposalId: null,
  pollTimer: null,
  lang: "en",
};

try {
  state.token = sessionStorage.getItem("nipta.token");
  state.operator = sessionStorage.getItem("nipta.operator");
  state.lang = localStorage.getItem("nipta.lang") || "en";
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

function signOut() {
  state.token = null;
  state.operator = null;
  try { sessionStorage.removeItem("nipta.token"); sessionStorage.removeItem("nipta.operator"); } catch (_) {}
  renderSession();
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
  } catch (err) { toast(err.message); }
});

$("#lang").value = state.lang;
$("#lang").addEventListener("change", () => {
  state.lang = $("#lang").value;
  try { localStorage.setItem("nipta.lang", state.lang); } catch (_) {}
  refreshBatch();
  if (state.selected) showInvoice(state.selected);
  if (state.proposalId) loadProposal(state.proposalId);
});

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
  state.batch = b;
  const groups = [["BLOCKED", "bad", "Blocked"], ["REVIEW", "warn", "Needs a check"], ["ELIGIBLE", "ok", "Safe to pay"]];
  const sum = (list) => list.reduce((t, i) => t + (i.stated_total_paise || 0), 0);
  const by = (k) => b.invoices.filter((i) => i.decision === k);
  $("#batchSummary").innerHTML = groups.map(([k, cls, label]) => `
    <div class="stat"><span class="label"><span class="dot ${cls}"></span>${esc(label.split(" ")[0])}</span>
      <div class="n">${by(k).length}</div><div class="amt">${by(k).length ? inr(sum(by(k))) : "—"}</div></div>`).join("");
  const bill = (i) => {
    const open = i.findings.find((f) => f.status === "OPEN");
    const why = i.payment_overlay ? i.payment_overlay.replace(/_/g, " ").toLowerCase()
      : open ? open.explanation : `${i.invoice_number || ""}${i.due_date ? ` · due ${i.due_date}` : ""}`;
    const ini = (i.vendor || "?").split(/\s+/).map((w) => w[0]).join("").slice(0, 2).toUpperCase();
    return `<button class="bill ${i.invoice_id === state.selected ? "selected" : ""}" data-id="${esc(i.invoice_id)}">
      <span class="ini" aria-hidden="true">${esc(ini)}</span>
      <span class="name">${esc(i.vendor || "Unknown vendor")}</span><span class="amt">${inr(i.stated_total_paise)}</span>
      <span class="why">${esc(why)}</span></button>`;
  };
  $("#billList").innerHTML = groups.filter(([k]) => by(k).length).map(([k, cls, label]) => `
    <div class="group-head"><span class="dot ${cls}"></span><span class="label">${esc(label)}</span><span class="amt">${inr(sum(by(k)))}</span></div>
    <div class="bill-list">${by(k).map(bill).join("")}</div>`).join("")
    + (b.invoices.length ? `<div class="total-row"><span class="label">All bills</span><span class="v">${inr(sum(b.invoices))}</span></div>`
      : `<p class="muted" style="font-weight:600;margin-top:18px">No bills yet. Add this Friday's bills or load the sample.</p>`)
    + (b.unprocessed_documents.length ? `<p class="small muted">${b.unprocessed_documents.length} not yet checked. Press Run checks.</p>` : "");
  document.querySelectorAll("#billList .bill").forEach((el) => el.onclick = () => showInvoice(el.dataset.id));
  const hasBills = b.invoices.length > 0;
  $("#welcome").classList.toggle("has-bills", hasBills);
  $("#introText").hidden = hasBills;
  $("#sixHead").hidden = !hasBills;
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
  if (r.invoices.length === 1) showInvoice(r.invoices[0].invoice_id);
  return r;
}

$("#upload").addEventListener("change", async (e) => {
  const files = [...e.target.files];
  e.target.value = "";
  if (!files.length) return;
  const fd = new FormData();
  files.forEach((f) => fd.append("files", f));
  try {
    const r = await api("/api/batches", { method: "POST", body: fd });
    const dups = r.documents.filter((d) => d.duplicate_upload);
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

// ---- invoice detail -----------------------------------------------------------

async function showInvoice(id) {
  state.selected = id;
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
        <a class="small" href="/api/invoices/${encodeURIComponent(d.invoice_id)}/source" target="_blank" rel="noopener">Original document</a>
        <button class="secondary" id="rerun" data-auth>Re-run checks</button>
        ${d.decision === "ELIGIBLE" ? `<button id="propose" data-auth>Prepare payment</button>` : ""}
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
      </dl>
    </div>
    ${checksHtml}
    <h3>Findings</h3>${findingsHtml}
    ${callHtml}
    ${lines ? `<h3>Line items</h3><div class="table-wrap"><table><thead><tr><th>Item</th><th class="num">Qty</th><th class="num">Unit</th><th class="num">Line total</th><th class="num">Recomputed</th></tr></thead><tbody>${lines}</tbody></table></div>` : ""}
    ${d.notes ? `<h3>Notes on document</h3><div class="note-data"><div class="small">${esc(d.notes_label)}</div>${esc(d.notes)}</div>` : ""}
  `;
  renderSession();
  el.querySelectorAll("[data-resolve]").forEach((b) => b.onclick = async () => {
    const reason = prompt("Why is this finding OK? (recorded in the audit log)");
    if (!reason) return;
    try {
      await api(`/api/findings/${b.dataset.resolve}/resolve`, { method: "POST", json: { reason } });
      await refreshBatch();
      showInvoice(id);
    } catch (err) { toast(err.message); }
  });
  $("#backHome").onclick = () => {
    state.selected = null;
    $("#detail").hidden = true;
    $("#approval").hidden = true;
    $("#welcome").hidden = false;
    document.querySelectorAll("#billList .bill").forEach((el) => el.classList.remove("selected"));
  };
  const callBtn = $("#vendorCall");
  if (callBtn) callBtn.onclick = async () => {
    try { await api(`/api/invoices/${id}/vendor-call?${lq()}`, { method: "POST" }); toast("Calling vendor"); showInvoice(id); }
    catch (err) { toast(err.message); }
  };
  // Poll until Relay posts the call result.
  if (vc && vc.status === "REQUESTED") setTimeout(() => { if (state.selected === id) showInvoice(id); }, 5000);
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
  if (d.proposal && ["READY_FOR_APPROVAL", "AUTHORIZED", "EXECUTED", "FINISHED"].includes(d.proposal.state)) {
    loadProposal(d.proposal.proposal_id);
  } else if (state.proposalId) {
    $("#approval").hidden = true;
    state.proposalId = null;
  }
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
      <h2>Approval card</h2>
      <span class="chip ${modeCls}">${esc(modeText)}</span>
    </div>
    <div class="grid2">
      <div>
        <div class="muted small">Pay ${esc(p.vendor)} · invoice ${esc(p.invoice_number)} (${esc(p.invoice_id)})</div>
        <div class="amount">${esc(p.amount_display)}</div>
        <div>to saved account <strong class="mono">${esc(p.beneficiary_display)}</strong></div>
        <div class="small"><span class="chip ${p.verification_label.startsWith("Live") ? "ok" : "muted"}">${esc(p.verification_label)}</span></div>
      </div>
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
    </div>
    <p><strong>${esc(p.status_text)}</strong></p>
    <div class="btn-row">
      ${p.state === "READY_FOR_APPROVAL" ? `<button id="approveBtn" data-auth>Approve ${esc(p.amount_display)}</button>` : ""}
      ${p.state === "AUTHORIZED" && !a ? `<button id="executeBtn" data-auth>Execute approved payout</button>` : ""}
      ${a && a.status === "REQUEST_RESERVED" && !a.dispatch_started_at && p.dispatch_mode === "CASHFREE_SANDBOX" ? `<button id="executeBtn" data-auth>Send reserved payout (same transfer ID)</button>` : ""}
      ${a && !TERMINAL.has(a.status) ? `<button id="refreshStatus" class="secondary">Check status</button>` : ""}
      <button id="speak" class="secondary" data-auth>Speak status</button>
    </div>
    ${timeline ? `<h3>Status timeline</h3><ol class="timeline">${timeline}</ol>` : ""}
    ${p.postings && p.postings.length ? `<h3>Postings</h3><ul>${p.postings.map((x) => `<li>${esc(x.kind)} ${inr(x.amount_paise)}</li>`).join("")}</ul>` : ""}
  `;
  renderSession();
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
  if (a && !TERMINAL.has(a.status)) state.pollTimer = setTimeout(() => loadProposal(p.proposal_id), 4000);
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

function openVendorDialog(inv) {
  const f = $("#vendorForm");
  f.reset();
  $("#vendorError").textContent = "";
  const hint = $("#vendorFromInvoice");
  if (inv) {
    f.display_name.value = inv.vendor_name_raw || "";
    f.account_holder_name.value = inv.vendor_name_raw || "";
    f.source_invoice_id.value = inv.invoice_id;
    const b = inv.bank_comparison;
    hint.hidden = false;
    hint.innerHTML = `From <span class="mono">${esc(inv.invoice_id)}</span>. The invoice prints account <span class="mono">${esc(b.printed_account || "none")}</span>
      ${b.printed_ifsc ? `IFSC <span class="mono">${esc(b.printed_ifsc)}</span>` : ""}. Printed details are only a claim: confirm the account with the supplier
      (e.g. by phone) and type it below. If it differs from the print, this invoice will be blocked as a changed account.`;
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
    <p class="small muted">Remaining ${inr(plan.remaining_paise)}. ${esc(plan.disclaimer)}</p>`;
  renderToday(plan);
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
    ...plan.selected.map((e) => row("ok", `Pay ${e.vendor}`, `#${e.rank} in this Friday's plan · due ${e.due_date || "—"}${e.overdue_days ? ` · ${e.overdue_days}d overdue` : ""}`, e.amount_paise, e.invoice_id, "Review & pay")),
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
    <p>${plan.selected.length} to pay now (${inr(payNow)}), ${n("REVIEW")} to check, ${n("BLOCKED")} blocked. Cash left after the plan: ${inr(plan.remaining_paise)}.</p>
    <ol class="todo-list">${items.join("")}</ol>`;
  document.querySelectorAll("#today [data-open]").forEach((btn) => btn.onclick = () => showInvoice(btn.dataset.open));
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
loadHealth().catch((e) => toast(e.message));
loadBatches().catch((e) => toast(e.message));
loadVendors().catch((e) => toast(e.message));
