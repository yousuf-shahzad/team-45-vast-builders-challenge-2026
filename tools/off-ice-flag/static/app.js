"use strict";
// Lane C: replay page for Off-ice Flag (contract version 2). No build step.
// All URLs are relative to the page, because Ingress serves the app under /app/.

const $ = (id) => document.getElementById(id);
const BASE = location.pathname.endsWith("/") ? location.pathname : location.pathname + "/";
const WORD = { come_off: "Come off", watch: "Watch", play_on: "Play on" };
const SIGNS = [
  ["head_neck", "Head or neck"],
  ["slow_to_rise", "Slow to rise"],
  ["left_ice", "Left the ice"],
  ["favoring_limb", "Favoring a limb"],
];
const HOLD_MS = { play_on: 700, watch: 1600 }; // how long a card stays up before the tape moves on
const POLL_MS = 15000;
const RANK = { come_off: 2, watch: 1, play_on: 0 };

const video = $("video");
let tape = null;
let cards = [];
let curId = null;
let playing = false;
let waiting = false; // reached the end while VAST is still indexing
let clipFinished = false;
let holdTimer = null;
let simTimer = null;
let pollTimer = null;
let filter = "all";
let sort = "time";
let saved = [];
const whyCache = new Map();
const detCache = new Map();

// --- helpers ---------------------------------------------------------------

const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
const url = (path) => BASE + path.replace(/^\//, "");
const curIndex = () => cards.findIndex((c) => c.id === curId);
const signCount = (c) => SIGNS.filter(([k]) => c.signs[k]).length;
const trueSigns = (c) => SIGNS.filter(([k]) => c.signs[k]).map(([, label]) => label);

function fmt(sec) {
  const s = Math.max(0, Math.floor(sec));
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), x = String(s % 60).padStart(2, "0");
  return h ? `${h}:${String(m).padStart(2, "0")}:${x}` : `${m}:${x}`;
}

function highlight(caption, quote) {
  const i = quote ? caption.toLowerCase().indexOf(quote.toLowerCase()) : -1;
  if (i < 0) return esc(caption);
  const j = i + quote.length;
  return `${esc(caption.slice(0, i))}<mark>${esc(caption.slice(i, j))}</mark>${esc(caption.slice(j))}`;
}

async function api(path, opts) {
  const r = await fetch(url(path), opts);
  let body = null;
  try { body = await r.json(); } catch { /* stream or empty body */ }
  return { ok: r.ok, status: r.status, body, fixture: r.headers.get("X-Off-Ice-Fixture") === "1" };
}

async function cached(map, id, path) {
  if (map.has(id)) return map.get(id);
  try {
    const r = await api(path);
    if (r.ok) { map.set(id, r.body); return r.body; }
  } catch { /* leave the line off */ }
  return null;
}

// --- tape ------------------------------------------------------------------

async function loadTape() {
  clearTimeout(pollTimer);
  let r = null;
  try { r = await api("api/tape"); } catch { /* network */ }
  if (!r || !r.ok) {
    setStatus("error", "Can't reach the tape. Retrying…");
    pollTimer = setTimeout(loadTape, POLL_MS);
    return;
  }
  tape = r.body;
  $("fixture").hidden = !r.fixture;
  const before = cards.length;
  cards = tape.cards.slice().sort((a, b) => a.start_sec - b.start_sec);
  renderStatus();
  renderTimeline();
  renderList();
  renderEmpty();
  updateTransport();
  loadQuestions();
  if (waiting && cards.length > before) { waiting = false; next(); }
  if (tape.status !== "ready") pollTimer = setTimeout(loadTape, POLL_MS);
}

function setStatus(kind, text) {
  const el = $("status");
  el.className = `pill s-${kind}`;
  el.textContent = text;
}

function renderStatus() {
  const n = cards.length, plural = n === 1 ? "card" : "cards";
  if (tape.status === "ready") setStatus("ready", `Ready · ${n} ${plural}`);
  else if (tape.status === "indexing") setStatus("indexing", `Indexing · ${n} ${plural} so far`);
  else setStatus("missing_clip", "No clip indexed yet");
}

function renderEmpty() {
  const empty = $("empty");
  if (curId) { empty.hidden = true; return; }
  empty.hidden = false;
  let text = "Press Play tape to start.";
  if (tape.status === "missing_clip") text = "Upload the game. This tape fills in while VAST indexes it.";
  else if (!cards.length && tape.status === "indexing") text = "Indexing. Cards appear here as segments are processed.";
  else if (!cards.length) text = "No contact found in this clip.";
  $("emptyText").textContent = text;
}

function renderTimeline() {
  const dur = tape.duration_sec || (cards.length ? cards[cards.length - 1].end_sec : 0) || 1;
  $("durLabel").textContent = fmt(dur);
  $("timeline").innerHTML = cards.map((c) => {
    const left = Math.min(100, (c.start_sec / dur) * 100).toFixed(3);
    const label = `${WORD[c.decision]} at ${fmt(c.start_sec)}`;
    return `<button type="button" class="tick d-${c.decision}${c.id === curId ? " cur" : ""}" style="left:${left}%" data-id="${esc(c.id)}" title="${label}" aria-label="${label}"></button>`;
  }).join("");
}

function renderList() {
  const counts = { all: cards.length, come_off: 0, watch: 0, play_on: 0 };
  cards.forEach((c) => counts[c.decision]++);
  document.querySelectorAll(".filters button").forEach((b) => {
    const f = b.dataset.f;
    b.textContent = `${f === "all" ? "All" : WORD[f]} ${counts[f]}`;
    b.setAttribute("aria-pressed", String(f === filter));
  });
  let rows = cards.filter((c) => filter === "all" || c.decision === filter);
  if (sort === "signs") {
    rows = rows.slice().sort((a, b) => signCount(b) - signCount(a) || RANK[b.decision] - RANK[a.decision] || a.start_sec - b.start_sec);
  }
  $("items").innerHTML = rows.map((c) => `
    <li><button type="button" class="item${c.id === curId ? " cur" : ""}" data-id="${esc(c.id)}">
      <span class="clock">${fmt(c.start_sec)}</span>
      <span class="decision sm d-${c.decision}">${WORD[c.decision]}</span>
      <span class="n" title="Visible signs">${signCount(c)}/4</span>
      <span class="snip">${esc(c.caption)}</span>
    </button></li>`).join("") || `<li class="none">${filter === "all" ? "No cards yet." : "No cards for this filter."}</li>`;
}

// --- card ------------------------------------------------------------------

function renderCard(c) {
  const n = signCount(c);
  $("card").innerHTML = `
    <div class="card-head">
      <span class="decision d-${c.decision}">${WORD[c.decision]}</span>
      <span class="clock">${fmt(c.start_sec)}–${fmt(c.end_sec)}</span>
    </div>
    <div class="score" aria-label="Visible signs ${n} of 4">
      <span class="pips">${SIGNS.map(([k]) => `<i class="${c.signs[k] ? "on" : ""}"></i>`).join("")}</span>
      <span>Visible signs <b>${n} of 4</b></span>
    </div>
    <ul class="signs">${SIGNS.map(([k, label]) =>
      `<li class="${c.signs[k] ? "yes" : "no"}"><span>${label}</span><b>${c.signs[k] ? "Yes" : "No"}</b></li>`).join("")}
    </ul>
    <figure class="caption"><figcaption>Caption</figcaption><p>${highlight(c.caption, c.evidence_quote)}</p></figure>
    <p class="why" id="why" hidden></p>
    <p class="inframe" id="inframe" hidden></p>
    <p class="note">Counts only the signs the caption states.</p>`;
  loadExtras(c);
}

async function loadExtras(c) {
  const id = encodeURIComponent(c.id);
  const [why, det] = await Promise.all([
    cached(whyCache, c.id, `api/cards/${id}/why`),
    cached(detCache, c.id, `api/segments/${id}/detections`),
  ]);
  if (curId !== c.id) return;
  if (why && why.sentence) { $("why").textContent = why.sentence; $("why").hidden = false; }
  if (det && det.available && det.objects.length) {
    $("inframe").textContent = "In frame: " + det.objects.map((o) => `${o.label} ${o.count}`).join(", ");
    $("inframe").hidden = false;
  }
}

// --- playback --------------------------------------------------------------

function clearTimers() {
  clearTimeout(holdTimer);
  clearTimeout(simTimer);
  holdTimer = simTimer = null;
}

function show(i) {
  const c = cards[i];
  if (!c) return;
  clearTimers();
  $("stop").hidden = true;
  $("noVideo").hidden = true;
  $("empty").hidden = true;
  clipFinished = false;
  curId = c.id;
  const chip = $("chip");
  chip.className = `chip d-${c.decision}`;
  chip.textContent = WORD[c.decision];
  chip.hidden = false;
  renderCard(c);
  renderTimeline();
  renderList();
  updateTransport();
  video.src = url(c.stream_path);
  video.play().catch(() => { /* error event starts the stand-in */ });
}

// No playable video (fixture tape, or the stream failed): hold the card for the clip's length.
function simulate() {
  const c = cards[curIndex()];
  if (!c) return;
  const secs = Math.min(8, Math.max(2, c.end_sec - c.start_sec));
  const bar = $("simBar");
  $("noVideo").hidden = false;
  bar.style.transition = "none";
  bar.style.width = "0";
  void bar.offsetWidth;
  bar.style.transition = `width ${secs}s linear`;
  bar.style.width = "100%";
  clearTimeout(simTimer);
  simTimer = setTimeout(clipDone, secs * 1000);
}

function clipDone() {
  clipFinished = true;
  if (!playing) { updateTransport(); return; }
  const c = cards[curIndex()];
  if (c.decision === "come_off") {
    playing = false;
    $("stopSigns").textContent = trueSigns(c).join(" · ") || "";
    $("stop").hidden = false;
    $("resume").focus();
    updateTransport();
    return;
  }
  holdTimer = setTimeout(next, HOLD_MS[c.decision]);
}

function next() {
  const i = curIndex();
  if (i + 1 < cards.length) { show(i + 1); return; }
  if (playing && tape.status !== "ready") { waiting = true; updateTransport(); return; }
  playing = false;
  updateTransport();
}

function prev() {
  const i = curIndex();
  if (i > 0) show(i - 1);
}

function toggle() {
  if (!cards.length) return;
  if (playing) {
    playing = false;
    waiting = false;
    clearTimers();
    video.pause();
    updateTransport();
    return;
  }
  playing = true;
  $("stop").hidden = true;
  const i = curIndex();
  if (i < 0) { show(0); return; }
  if (clipFinished) { next(); return; }
  if (!$("noVideo").hidden) simulate();
  else video.play().catch(() => {});
  updateTransport();
}

function resume() {
  $("stop").hidden = true;
  playing = true;
  next();
}

function openCard(id) {
  const i = cards.findIndex((c) => c.id === id);
  if (i >= 0) show(i);
}

function updateTransport() {
  const i = curIndex();
  const stopped = !$("stop").hidden;
  $("play").textContent = playing ? (waiting ? "Waiting for cards…" : "Pause") : (stopped ? "Resume tape" : "Play tape");
  $("play").disabled = !cards.length;
  $("prev").disabled = i <= 0;
  $("next").disabled = i < 0 || i >= cards.length - 1;
  $("pos").textContent = i >= 0 ? `Card ${i + 1} of ${cards.length}` : (cards.length ? `${cards.length} cards` : "");
}

// --- questions -------------------------------------------------------------

async function loadQuestions() {
  let r = null;
  try { r = await api("api/questions"); } catch { /* keep the old list */ }
  if (!r || !r.ok) return;
  saved = r.body.questions;
  $("saved").innerHTML = saved.map((q, i) =>
    `<li><button type="button" class="qbtn" data-i="${i}">${esc(q.question)}</button></li>`).join("")
    || `<li class="none">Saved answers appear once cards exist.</li>`;
}

function firstCard(q) {
  return q.card_ids.find((id) => cards.some((c) => c.id === id));
}

function showAnswer(q) {
  const box = $("answer");
  const target = firstCard(q);
  const card = target && cards.find((c) => c.id === target);
  box.className = "answer";
  box.hidden = false;
  box.innerHTML = `
    <p class="q">${esc(q.question)}</p>
    <p class="a">${esc(q.answer)}</p>
    <p class="meta">${esc(q.grounding)} · ${q.tools.map(esc).join(", ")}</p>
    ${card ? `<button type="button" class="link" data-id="${esc(card.id)}">Open the moment at ${fmt(card.start_sec)}</button>` : ""}`;
  if (target) openCard(target); // an answer seeks to its first card; none leaves the playhead
}

function showAnswerError(question, text) {
  const box = $("answer");
  box.className = "answer err";
  box.hidden = false;
  box.innerHTML = `<p class="q">${esc(question)}</p><p class="a">${esc(text)}</p>`;
}

async function ask(e) {
  e.preventDefault();
  const text = $("q").value.trim();
  if (!text) return;
  $("askBtn").disabled = true;
  const box = $("answer");
  box.className = "answer";
  box.hidden = false;
  box.innerHTML = `<p class="q">${esc(text)}</p><p class="a">Asking…</p>`;
  let r = null;
  try {
    r = await api("api/ask", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ question: text }) });
  } catch { /* network */ }
  $("askBtn").disabled = false;
  if (r && r.ok) showAnswer(r.body);
  else if (r && r.status === 409) showAnswerError(text, "Nothing is indexed for this game yet.");
  else showAnswerError(text, "No answer right now. Try a saved question.");
}

// --- wiring ----------------------------------------------------------------

video.addEventListener("ended", clipDone);
video.addEventListener("error", simulate);
$("play").addEventListener("click", toggle);
$("next").addEventListener("click", () => { const i = curIndex(); if (i + 1 < cards.length) show(i + 1); });
$("prev").addEventListener("click", prev);
$("resume").addEventListener("click", resume);
$("askForm").addEventListener("submit", ask);
$("sort").addEventListener("change", (e) => { sort = e.target.value; renderList(); });
document.querySelector(".filters").addEventListener("click", (e) => {
  const b = e.target.closest("button");
  if (b) { filter = b.dataset.f; renderList(); }
});
for (const id of ["timeline", "items", "answer"]) {
  $(id).addEventListener("click", (e) => {
    const b = e.target.closest("[data-id]");
    if (b) openCard(b.dataset.id);
  });
}
$("saved").addEventListener("click", (e) => {
  const b = e.target.closest(".qbtn");
  if (b) showAnswer(saved[+b.dataset.i]);
});
document.addEventListener("keydown", (e) => {
  if (e.target.closest("input, select, textarea, button")) return;
  if (e.key === " ") { e.preventDefault(); toggle(); }
  else if (e.key === "ArrowRight") $("next").click();
  else if (e.key === "ArrowLeft") prev();
});

loadTape();
