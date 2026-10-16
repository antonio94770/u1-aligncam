"use strict";

const $ = (id) => document.getElementById(id);
let current = null;      // last API response
let view = "overlay";

const um = (mm) => (mm === null || mm === undefined ? "-" : (mm * 1000).toFixed(0));
const signed = (mm) => (mm >= 0 ? "+" : "") + (mm * 1000).toFixed(0);

function esc(s) {
  return String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;",
    '"': "&quot;", "'": "&#39;" }[c]));
}

async function api(url, opts) {
  const r = await fetch(url, opts);
  const body = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(body.detail || `HTTP ${r.status}`);
  return body;
}

function busy(on) {
  $("busy").hidden = !on;
  for (const b of ["btn-sample", "btn-apply"]) $(b).disabled = on || (b === "btn-apply" && !canApply());
}

function showImage() {
  if (!current) return;
  $("img").src = view === "overlay" ? current.overlay : current.image;
  $("viewer").classList.remove("empty");
  $("view-overlay").classList.toggle("on", view === "overlay");
  $("view-raw").classList.toggle("on", view !== "overlay");
}

function chip(el, status) {
  el.textContent = status;
  el.className = "chip " + (status === "ok" ? "ok" : status === "partial" ? "warn" : "bad");
}

function canApply() {
  return !!current && current.proposal.corrections.some((c) => c.significant);
}

function render(res) {
  current = res;
  showImage();
  const m = res.measurement;
  $("src-title").textContent = res.source;
  chip($("status-chip"), m.status);

  const rows = Object.entries(m.tools).map(([name, t]) => {
    const truth = res.truth && res.truth[name];
    const tx = truth ? `<span class="truth">true ${signed(truth[0])}</span>` : "";
    const ty = truth ? `<span class="truth">true ${signed(truth[1])}</span>` : "";
    const reasons = t.reasons.length ? `<span class="reasons">${t.reasons.map(esc).join(", ")}</span>` : "";
    const val = (v, s) => (v === null ? "-" : `${signed(v)} <span class="sig">± ${um(s)}</span>`);
    return `<tr><td>${esc(name)}</td><td>${val(t.dx_mm, t.sigma_x_mm)}${tx}</td>` +
      `<td>${val(t.dy_mm, t.sigma_y_mm)}${ty}</td>` +
      `<td><span class="chip ${t.status === "ok" ? "ok" : "bad"}">${esc(t.status)}</span>${reasons}</td></tr>`;
  });
  $("tool-rows").innerHTML = rows.join("") || `<tr><td colspan="4" class="muted">-</td></tr>`;

  $("cell-rows").innerHTML = m.cells.map((c) =>
    `<tr><td>${esc(c.name)}${c.control ? " (ctrl)" : ""}</td><td>${um(c.offset_mm)}</td>` +
    `<td>${um(c.vernier_offset_mm)}</td><td>${c.snr ?? "-"}</td><td>${esc(c.status)}</td></tr>`).join("");

  const w = [...m.warnings, ...m.reasons];
  $("warnings").hidden = !w.length;
  $("warnings").textContent = w.join(" · ");

  const p = res.proposal;
  const items = p.corrections.map((c) =>
    `<li>T${c.tool}: X ${signed(c.delta_x_mm)} um, Y ${signed(c.delta_y_mm)} um` +
    (c.significant ? "" : ` <span class="skip">(within uncertainty, no change)</span>`) + "</li>");
  for (const [t, why] of Object.entries(p.skipped)) {
    items.push(`<li class="skip">${esc(t)}: no proposal (${why.map(esc).join(", ")})</li>`);
  }
  $("proposal").innerHTML = items.length ? `<ul>${items.join("")}</ul>` : "-";
  $("btn-apply").disabled = !canApply();
  $("apply-log").hidden = true;
}

async function runSample() {
  const q = new URLSearchParams({
    seed: $("seed").value, max_offset_mm: $("maxoff").value,
    gsd: $("gsd").value, tilt: $("tilt").value, blur: $("blur").value,
  });
  busy(true);
  try { render(await api(`/api/sample?${q}`)); }
  catch (e) { alert(e.message); }
  finally { busy(false); }
}

async function upload(file) {
  const fd = new FormData();
  fd.append("file", file);
  busy(true);
  try { render(await api("/api/measure", { method: "POST", body: fd })); }
  catch (e) { alert(e.message); }
  finally { busy(false); }
}

async function runPrinter() {
  busy(true);
  $("busy").textContent = "printing pattern, then measuring…";
  try { render(await api("/api/printer/calibrate", { method: "POST" })); }
  catch (e) { alert(e.message); }
  finally { busy(false); $("busy").textContent = "measuring…"; }
}

function openConfirm() {
  const sig = current.proposal.corrections.filter((c) => c.significant);
  $("confirm-list").innerHTML = sig.map((c) =>
    `<li><label class="check"><input type="checkbox" data-tool="${c.tool}" checked> ` +
    `T${c.tool}: X ${signed(c.delta_x_mm)} um, Y ${signed(c.delta_y_mm)} um</label></li>`).join("");
  $("confirm-check").checked = false;
  $("confirm-ok").disabled = true;
  // Closing with Esc keeps the previous returnValue: reset it so only an explicit
  // click on Confirm can apply.
  $("confirm").returnValue = "";
  $("confirm").showModal();
}

async function doApply() {
  const tools = [...$("confirm-list").querySelectorAll("input[data-tool]")]
    .filter((i) => i.checked).map((i) => Number(i.dataset.tool));
  try {
    const r = await api("/api/offsets/apply", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ digest: current.proposal.digest, confirmed_by: "web user", tools }),
    });
    $("apply-log").textContent = r.lines.join("\n");
    $("apply-log").hidden = false;
  } catch (e) { alert(e.message); }
}

function bindSlider(id) {
  $(id).addEventListener("input", () => { $(id + "-v").textContent = $(id).value; });
}

async function init() {
  ["gsd", "tilt", "blur"].forEach(bindSlider);
  $("btn-sample").onclick = runSample;
  $("file").onchange = (e) => e.target.files[0] && upload(e.target.files[0]);
  $("btn-printer").onclick = runPrinter;
  $("view-overlay").onclick = () => { view = "overlay"; showImage(); };
  $("view-raw").onclick = () => { view = "raw"; showImage(); };
  $("btn-apply").onclick = openConfirm;
  $("confirm-check").onchange = (e) => { $("confirm-ok").disabled = !e.target.checked; };
  $("confirm").addEventListener("close", () => {
    if ($("confirm").returnValue === "ok") doApply();
  });
  try {
    const info = await api("/api/info");
    if (info.moonraker) {
      $("printer-chip").textContent = "Moonraker: " + info.moonraker;
      $("printer-chip").className = "chip ok";
      $("btn-printer").disabled = false;
    }
  } catch (e) { /* demo still works offline */ }
  await autorun(new URLSearchParams(location.search));
}

// Shareable/scriptable states: ?seed=3&gsd=0.2&tilt=30&blur=1&max=0.2&run=sample&view=raw
// (run=printer uses the configured Moonraker; confirm=1 opens the confirmation dialog).
async function autorun(q) {
  const set = (key, id) => {
    if (q.has(key)) { $(id).value = q.get(key); $(id).dispatchEvent(new Event("input")); }
  };
  set("seed", "seed"); set("max", "maxoff"); set("gsd", "gsd"); set("tilt", "tilt"); set("blur", "blur");
  if (q.get("view") === "raw") view = "raw";
  if (q.get("run") === "sample") await runSample();
  if (q.get("run") === "printer" && !$("btn-printer").disabled) await runPrinter();
  if (q.get("confirm") === "1" && canApply()) openConfirm();
  document.body.dataset.ready = "1";
}

init();
