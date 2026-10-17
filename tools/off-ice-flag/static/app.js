"use strict";
// Lane C: Off-ice Flag replay page (contract version 2). No build step.
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
let waiting = false; // reached the end while the game is still being processed
let clipFinished = false;
let holdTimer = null;
let simTimer = null;
let pollTimer = null;
let filter = "all";
let sort = "time";
let matches = null; // card ids from the last search, best first
let saved = [];
let evalLoaded = false;
let source = "chunks"; // the tab: "chunks" (Split game) or "whole" (Whole video)
let sourcesInfo = null; // GET api/sources: what each tab has loaded
let library = []; // GET api/library: uploads in VAST, for the loader
const whyCache = new Map();
const detCache = new Map();

// --- helpers ---------------------------------------------------------------

const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
const url = (path) => `${BASE}${path.replace(/^\//, "")}${path.includes("?") ? "&" : "?"}source=${source}`;
const fileName = (uri) => uri.split("/").pop();
const byId = (id) => cards.find((c) => c.id === id);
const curIndex = () => cards.findIndex((c) => c.id === curId);
const signCount = (c) => SIGNS.filter(([k]) => c.signs[k]).length;
const trueSigns = (c) => SIGNS.filter(([k]) => c.signs[k]).map(([, label]) => label);
const plural = (n, word, many = word + "s") => `${n} ${n === 1 ? word : many}`;
const duration = () => tape.duration_sec || (cards.length ? cards[cards.length - 1].end_sec : 0);

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
  try { body = await r.json(); } catch { /* empty body */ }
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

const whyOf = (id) => cached(whyCache, id, `api/cards/${encodeURIComponent(id)}/why`);
const detOf = (id) => cached(detCache, id, `api/segments/${encodeURIComponent(id)}/detections`);

// --- tape ------------------------------------------------------------------

async function loadTape() {
  clearTimeout(pollTimer);
  let r = null;
  try { r = await api("api/tape"); } catch { /* network */ }
  if (!r || !r.ok) {
    setStatus("error", "Can't reach the tape · retrying");
    pollTimer = setTimeout(loadTape, POLL_MS);
    return;
  }
  tape = r.body;
  $("fixture").hidden = !r.fixture;
  const before = cards.length;
  cards = tape.cards.slice().sort((a, b) => a.start_sec - b.start_sec);
  renderHeader();
  renderTimeline();
  renderLog();
  renderSummary();
  renderEmpty();
  updateTransport();
  loadQuestions();
  if (tape.status === "ready" && !evalLoaded) loadEval();
  if (tape.status !== "ready") renderEvalPending();
  if (waiting && cards.length > before) { waiting = false; next(); }
  if (tape.status !== "ready") pollTimer = setTimeout(loadTape, POLL_MS);
}

function setStatus(kind, text) {
  const el = $("status");
  el.className = `tag status s-${kind}`;
  el.textContent = text;
}

function renderHeader() {
  const game = $("game");
  game.textContent = tape.video_id;
  game.hidden = !tape.video_id;
  if (tape.status === "ready") setStatus("ready", `Game processed · ${Math.max(1, Math.round(duration() / 60))} min`);
  else if (tape.status === "indexing") setStatus("indexing", `Processing · ${plural(cards.length, "card")} so far`);
  else setStatus("missing_clip", "No game indexed yet");
}

function renderEmpty() {
  const empty = $("empty");
  if (curId) { empty.hidden = true; return; }
  empty.hidden = false;
  let text = "Press Play tape, or pick a moment below.";
  if (tape.status === "missing_clip") {
    text = source === "chunks"
      ? "Nothing loaded in this tab yet. Use Load videos to pick the game's parts."
      : "Nothing loaded in this tab yet. Use Load videos to pick one video.";
  }
  else if (!cards.length && tape.status === "indexing") text = "Processing the game. Moments appear here as segments are indexed.";
  else if (!cards.length) text = "No contact found in this game.";
  $("emptyText").textContent = text;
}

function renderTimeline() {
  const dur = duration() || 1;
  const quarters = [0, 0.25, 0.5, 0.75, 1];
  const dividers = quarters.slice(1, -1).map((f) => `<span class="div" style="left:${f * 100}%"></span>`).join("");
  $("timeline").innerHTML = dividers + cards.map((c) => {
    const left = Math.min(99.2, Math.max(0.8, (c.start_sec / dur) * 100)).toFixed(3);
    const label = `${WORD[c.decision]} at ${fmt(c.start_sec)}`;
    const dim = matches && !matches.includes(c.id) ? " dim" : "";
    return `<button type="button" class="mk d-${c.decision}${c.id === curId ? " cur" : ""}${dim}" style="left:${left}%" data-id="${esc(c.id)}" title="${label}" aria-label="${label}"></button>`;
  }).join("");
  $("axis").innerHTML = quarters.map((f) => `<span style="left:${f * 100}%">${fmt(f * dur)}</span>`).join("");
}

function renderLog() {
  const counts = { all: cards.length, come_off: 0, watch: 0, play_on: 0 };
  cards.forEach((c) => counts[c.decision]++);
  document.querySelectorAll(".filters button").forEach((b) => {
    const f = b.dataset.f;
    b.textContent = `${f === "all" ? "All" : WORD[f]} ${counts[f]}`;
    b.setAttribute("aria-pressed", String(f === filter));
  });
  let rows;
  if (matches) {
    rows = matches.map(byId).filter(Boolean);
  } else {
    rows = cards.filter((c) => filter === "all" || c.decision === filter);
    if (sort === "signs") {
      rows = rows.slice().sort((a, b) => signCount(b) - signCount(a) || RANK[b.decision] - RANK[a.decision] || a.start_sec - b.start_sec);
    }
  }
  $("rows").innerHTML = rows.map((c) => `
    <tr data-id="${esc(c.id)}" class="${c.id === curId ? "cur" : ""}" tabindex="0">
      <td class="mono">${fmt(c.start_sec)}</td>
      <td><span class="pill d-${c.decision}">${WORD[c.decision]}</span></td>
      <td class="what"><span>${esc(c.caption)}</span></td>
      <td class="mono col-signs">${signCount(c)}/4</td>
      <td><button type="button" class="link" data-id="${esc(c.id)}">Play clip</button></td>
    </tr>`).join("")
    || `<tr><td colspan="5" class="none">${matches ? "No moments match that search." : cards.length ? "No moments for this filter." : "No moments yet."}</td></tr>`;
}

function renderSummary() {
  const n = { come_off: 0, watch: 0, play_on: 0 };
  cards.forEach((c) => n[c.decision]++);
  $("nCome").textContent = n.come_off;
  $("nWatch").textContent = n.watch;
  $("nPlay").textContent = n.play_on;
  const recap = saved.find((q) => q.id === "clip-recap");
  $("recap").textContent = recap ? recap.answer
    : tape.status === "ready" ? "No recap for this game." : "The recap is written once the whole game is processed.";
  const flagged = cards.filter((c) => c.decision !== "play_on");
  const secs = flagged.reduce((t, c) => t + (c.end_sec - c.start_sec), 0);
  const dur = duration();
  $("reviewTime").textContent = dur
    ? `Reviewing the ${plural(flagged.length, "come off and watch moment")} takes about ${Math.max(1, Math.round(secs / 60))} min instead of ${Math.max(1, Math.round(dur / 60))} min of game.`
    : "";
}

// --- eval ------------------------------------------------------------------

function renderEvalPending() {
  $("evalRows").innerHTML = "";
  $("evalNote").textContent = "Scored once the game is processed: eight moments labelled by hand, matched within 5 s.";
}

async function loadEval() {
  let r = null;
  try { r = await api("api/eval"); } catch { /* keep pending */ }
  if (!r || !r.ok) {
    $("evalRows").innerHTML = "";
    $("evalNote").textContent = r && r.status === 409
      ? "Waiting for hand labels (labels.json): eight moments, matched within 5 s."
      : "Score unavailable right now.";
    return;
  }
  evalLoaded = true;
  const e = r.body;
  const signMisses = e.misses.filter((m) => m.reason.includes("signs differ")).length;
  const rows = [
    ["Moments labelled by hand", e.total],
    ["Decisions matched", `${e.matched} / ${e.total}`],
    ["Decision right, signs off", signMisses],
    ["Other misses", e.misses.length - signMisses],
  ];
  $("evalRows").innerHTML = rows.map(([k, v]) => `<div><dt>${k}</dt><dd>${esc(v)}</dd></div>`).join("");
  $("evalNote").textContent = r.fixture
    ? "Sample labels on sample cards, not the judged game."
    : "Scores the decision on each card, not the why sentences or answers.";
}

// --- card ------------------------------------------------------------------

function renderCard(c) {
  const n = signCount(c);
  const el = $("card");
  el.hidden = false;
  el.innerHTML = `
    <div class="cv-head">
      <span class="pill d-${c.decision}">${WORD[c.decision]}</span>
      <span class="mono muted">${fmt(c.start_sec)}–${fmt(c.end_sec)}</span>
      <span class="score" aria-label="Visible signs ${n} of 4">
        <span class="pips">${SIGNS.map(([k]) => `<i class="${c.signs[k] ? "on" : ""}"></i>`).join("")}</span>
        Visible signs <b>${n} of 4</b>
      </span>
    </div>
    <div class="cv-body">
      <ul class="signs">${SIGNS.map(([k, label]) =>
        `<li class="${c.signs[k] ? "yes" : "no"}"><span>${label}</span><b>${c.signs[k] ? "Yes" : "No"}</b></li>`).join("")}
      </ul>
      <div class="cv-text">
        <p class="caption"><span class="lbl">Caption</span>${highlight(c.caption, c.evidence_quote)}</p>
        <p class="why" id="why" hidden></p>
        <p class="inframe" id="inframe" hidden></p>
        <p class="fine">Counts only the signs the caption states.</p>
      </div>
    </div>`;
  loadExtras(c);
}

async function loadExtras(c) {
  const [why, det] = await Promise.all([whyOf(c.id), detOf(c.id)]);
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
  chip.textContent = `${WORD[c.decision]} · ${signCount(c)}/4 signs`;
  chip.hidden = false;
  $("clock").textContent = fmt(c.start_sec);
  $("clock").hidden = false;
  renderCard(c);
  renderTimeline();
  renderLog();
  updateTransport();
  video.src = url(c.stream_path);
  video.play().catch(() => { /* the error event starts the stand-in */ });
}

// No playable video (sample tape, or the stream failed): hold the card for the clip's length.
function simulate() {
  const c = byId(curId);
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
  const c = byId(curId);
  if (c.decision === "come_off") {
    playing = false;
    $("stopSigns").textContent = trueSigns(c).join(" · ");
    $("stop").hidden = false;
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
  if (curIndex() < 0) { show(0); return; }
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
  if (i < 0) return;
  show(i);
  $("video").closest(".player").scrollIntoView({ behavior: "smooth", block: "nearest" });
}

function updateTransport() {
  const i = curIndex();
  const stopped = !$("stop").hidden;
  $("play").textContent = playing ? (waiting ? "Waiting for moments…" : "Pause") : (stopped ? "Resume tape" : "Play tape");
  $("play").disabled = !cards.length;
  $("prev").disabled = i <= 0;
  $("next").disabled = i < 0 || i >= cards.length - 1;
  $("pos").textContent = i >= 0 ? `${i + 1} / ${cards.length}` : "";
}

// --- search ----------------------------------------------------------------

async function search(e) {
  e.preventDefault();
  const q = $("sq").value.trim();
  if (!q) { clearSearch(); return; }
  $("searchNote").textContent = "Searching…";
  let r = null;
  try { r = await api(`api/search?q=${encodeURIComponent(q)}`); } catch { /* network */ }
  if (!r || !r.ok) {
    $("searchNote").textContent = r && r.status === 409 ? "Nothing is indexed for this game yet." : "Search is unavailable right now.";
    return;
  }
  matches = r.body.card_ids;
  const where = r.fixture ? "in the sample captions" : "· NVIDIA Cosmos captions, searched on VAST";
  $("searchNote").textContent = `${plural(matches.length, "match", "matches")} ${where}`;
  $("searchChip").innerHTML = `Showing ${plural(matches.length, "match", "matches")} for “${esc(q)}”, best first · <button type="button" class="link" id="clearSearch">Clear</button>`;
  $("searchChip").hidden = false;
  renderTimeline();
  renderLog();
  if (matches.length) openCard(matches[0]);
}

function clearSearch() {
  matches = null;
  $("searchChip").hidden = true;
  $("searchNote").textContent = "Plain-language search over every moment of the game · NVIDIA Cosmos captions, searched on VAST";
  renderTimeline();
  renderLog();
}

// --- agent -----------------------------------------------------------------

const STEP = {
  rule: (n) => `decision rule picked ${plural(n, "card")} from the visible signs`,
  wandb: () => "one why sentence per card, checked in Python",
  detections: (n) => `stored YOLO labels for ${plural(n, "moment")}`,
  synthesize: () => "VAST summary over the game's captions",
  agent_ask: (n) => `VAST agent over this game's segments → ${plural(n, "moment")}`,
};

async function loadQuestions() {
  let r = null;
  try { r = await api("api/questions"); } catch { /* keep the old list */ }
  if (!r || !r.ok) return;
  saved = r.body.questions;
  $("chips").innerHTML = saved.map((q, i) => `<button type="button" data-i="${i}">${esc(q.question)}</button>`).join("");
  renderSummary();
}

function addBubble(text) {
  const thread = $("thread");
  thread.querySelector(".intro")?.remove();
  const wrap = document.createElement("div");
  wrap.className = "exchange";
  wrap.innerHTML = `<div class="bubble">${esc(text)}</div><div class="reply">Thinking…</div>`;
  thread.appendChild(wrap);
  thread.scrollTop = thread.scrollHeight;
  return wrap;
}

async function renderReply(wrap, q) {
  const ids = q.card_ids.filter((id) => byId(id));
  const steps = q.tools.map((t) => `<li><code>${esc(t)}</code>${esc((STEP[t] || (() => ""))(ids.length))}</li>`).join("");
  let body = `<p>${esc(q.answer)}</p>`;
  if (q.grounding === "decision" && ids.length) {
    const whys = await Promise.all(ids.map(whyOf));
    if (whys.every((w) => w && w.sentence)) {
      body = `<p>${plural(ids.length, "moment")} to look at:</p><ul class="hits">${ids.map((id, k) => {
        const c = byId(id);
        return `<li><span class="dot d-${c.decision}"></span><div><b class="mono">${fmt(c.start_sec)}</b> – ${esc(whys[k].sentence)}
          <button type="button" class="link" data-id="${esc(id)}">Play clip</button></div></li>`;
      }).join("")}</ul>`;
    }
  } else if (ids.length) {
    body += `<p>${ids.map((id) => `<button type="button" class="link mono" data-id="${esc(id)}">${fmt(byId(id).start_sec)}</button>`).join(" · ")}</p>`;
  }
  wrap.innerHTML = `
    <div class="bubble">${esc(q.question)}</div>
    <div class="steps"><p class="lbl">Agent steps</p><ul>${steps}</ul></div>
    <div class="reply">${body}
      <p class="foot">${esc(q.grounding)} · ${q.tools.map(esc).join(", ")} · flagged for a trainer to review</p>
    </div>`;
  $("thread").scrollTop = $("thread").scrollHeight;
  if (ids.length) openCard(ids[0]); // an answer opens its first card; none leaves the playhead
}

function replyError(wrap, text) {
  const r = wrap.querySelector(".reply");
  r.className = "reply err";
  r.textContent = text;
}

async function ask(e) {
  e.preventDefault();
  const text = $("q").value.trim();
  if (!text) return;
  $("q").value = "";
  $("askBtn").disabled = true;
  const wrap = addBubble(text);
  let r = null;
  try {
    r = await api("api/ask", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ question: text }) });
  } catch { /* network */ }
  $("askBtn").disabled = false;
  if (r && r.ok) await renderReply(wrap, r.body);
  else if (r && r.status === 409) replyError(wrap, "Nothing is indexed for this game yet.");
  else replyError(wrap, "No answer right now. Try one of the saved questions.");
}

// --- tabs + loader -----------------------------------------------------------

function remember(value) {
  try { localStorage.setItem("offIceTab", value); } catch { /* private window: fine */ }
}

function recalled() {
  try { return localStorage.getItem("offIceTab"); } catch { return null; }
}

function resetGame() {
  clearTimeout(pollTimer);
  clearTimers();
  playing = waiting = clipFinished = false;
  curId = null;
  cards = [];
  matches = null;
  saved = [];
  evalLoaded = false;
  whyCache.clear();
  detCache.clear();
  video.removeAttribute("src");
  video.load();
  for (const id of ["card", "chip", "clock", "stop", "noVideo", "searchChip"]) $(id).hidden = true;
  $("thread").innerHTML = `<p class="muted small intro">Ask about a hit, a sign, or the whole game. Answers come from the cards and from VAST, and link back to the clip.</p>`;
  $("chips").innerHTML = "";
}

function renderLoaded() {
  const videos = sourcesInfo ? sourcesInfo.sources[source] : [];
  let text;
  if (!sourcesInfo || sourcesInfo.fixture) text = "Showing sample data · nothing loaded yet";
  else if (!videos.length) text = "Nothing loaded in this tab";
  else if (source === "chunks") text = `${plural(videos.length, "part")} · ${fileName(videos[0])} … ${fileName(videos[videos.length - 1])}`;
  else text = fileName(videos[0]);
  $("loadedText").textContent = text;
  $("loadedText").title = videos.map(fileName).join("\n");
}

function setTab(value) {
  source = value === "whole" ? "whole" : "chunks";
  remember(source);
  document.querySelectorAll(".tabs button").forEach((b) => b.setAttribute("aria-selected", String(b.dataset.source === source)));
  $("loader").hidden = true;
  resetGame();
  renderLoaded();
  loadTape();
}

async function openLoader() {
  const multi = source === "chunks";
  $("loaderTitle").textContent = multi ? "Load the split game" : "Load a whole video";
  $("loaderHint").textContent = multi
    ? "Tick every part. They play in file-name order, so part_000 comes first."
    : "Pick one video, at most the 100 MB upload cap.";
  $("loader").hidden = false;
  $("libList").innerHTML = `<li class="none muted">Listing uploads in VAST…</li>`;
  let r = null;
  try { r = await api("api/library"); } catch { /* network */ }
  if (!r || !r.ok) {
    $("libList").innerHTML = `<li class="none muted">Can't list videos from VAST right now.</li>`;
    return;
  }
  library = r.body.videos.slice().sort((a, b) => a.filename.localeCompare(b.filename));
  $("libAll").checked = !library.some((v) => v.ours);
  renderLibrary(new Set(sourcesInfo ? sourcesInfo.sources[source] : []));
}

function chosen() {
  return [...$("libList").querySelectorAll("input:checked")].map((i) => i.value);
}

function renderLibrary(selected = new Set(chosen())) {
  const multi = source === "chunks";
  const f = $("libFilter").value.trim().toLowerCase();
  const rows = library.filter((v) => ($("libAll").checked || v.ours || selected.has(v.original_video))
    && (!f || v.filename.toLowerCase().includes(f)));
  $("libList").innerHTML = rows.map((v) => `
    <li><label>
      <input type="${multi ? "checkbox" : "radio"}" name="lib" value="${esc(v.original_video)}"${selected.has(v.original_video) ? " checked" : ""}>
      <span class="order"></span>
      <span class="fname" title="${esc(v.original_video)}">${esc(v.filename || fileName(v.original_video))}</span>
      <span class="muted small">${plural(v.clips, "clip")}</span>
    </label></li>`).join("") || `<li class="none muted">No indexed uploads match. Uploads show here once VAST has indexed them.</li>`;
  numberChosen();
}

function numberChosen() {
  const picks = chosen();
  $("libList").querySelectorAll("input").forEach((i) => {
    i.closest("label").querySelector(".order").textContent = i.checked && source === "chunks" ? String(picks.indexOf(i.value) + 1).padStart(2, "0") : "";
  });
  $("libCount").textContent = source === "chunks"
    ? `${plural(picks.length, "part")} selected, played in file-name order`
    : (picks.length ? "1 video selected" : "No video selected");
  $("libLoad").disabled = !picks.length;
}

async function loadSelected() {
  const byName = new Map(library.map((v) => [v.original_video, v.filename]));
  const videos = chosen().sort((a, b) => (byName.get(a) || a).localeCompare(byName.get(b) || b));
  $("libLoad").disabled = true;
  let r = null;
  try {
    r = await api(`api/sources/${source}`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ videos }) });
  } catch { /* network */ }
  $("libLoad").disabled = false;
  if (!r || !r.ok) {
    $("loaderHint").textContent = (r && r.body && r.body.error) || "Loading failed. Try again.";
    return;
  }
  sourcesInfo = r.body;
  $("loader").hidden = true;
  resetGame();
  renderLoaded();
  loadTape();
}

async function init() {
  try {
    const r = await api("api/sources");
    if (r.ok) sourcesInfo = r.body;
  } catch { /* the tape call reports it */ }
  const stored = recalled();
  setTab(stored === "chunks" || stored === "whole" ? stored : sourcesInfo ? sourcesInfo.default : "chunks");
}

// --- wiring ----------------------------------------------------------------

video.addEventListener("ended", clipDone);
video.addEventListener("error", simulate);
$("play").addEventListener("click", toggle);
$("next").addEventListener("click", () => { const i = curIndex(); if (i + 1 < cards.length) show(i + 1); });
$("prev").addEventListener("click", prev);
$("resume").addEventListener("click", resume);
$("askForm").addEventListener("submit", ask);
$("searchForm").addEventListener("submit", search);
$("sq").addEventListener("search", () => { if (!$("sq").value) clearSearch(); });
$("sort").addEventListener("change", (e) => { sort = e.target.value; renderLog(); });
document.querySelector(".filters").addEventListener("click", (e) => {
  const b = e.target.closest("button");
  if (!b) return;
  filter = b.dataset.f;
  if (matches) clearSearch(); else renderLog();
});
$("searchChip").addEventListener("click", (e) => { if (e.target.id === "clearSearch") clearSearch(); });
for (const id of ["timeline", "rows", "thread"]) {
  $(id).addEventListener("click", (e) => {
    const b = e.target.closest("[data-id]");
    if (b) openCard(b.dataset.id);
  });
}
$("rows").addEventListener("keydown", (e) => {
  const tr = e.target.closest("tr[data-id]");
  if (tr && (e.key === "Enter" || e.key === " ")) { e.preventDefault(); openCard(tr.dataset.id); }
});
$("chips").addEventListener("click", (e) => {
  const b = e.target.closest("button[data-i]");
  if (b) renderReply(addBubble(saved[+b.dataset.i].question), saved[+b.dataset.i]);
});
document.querySelector(".tabs").addEventListener("click", (e) => {
  const b = e.target.closest("button[data-source]");
  if (b && b.dataset.source !== source) setTab(b.dataset.source);
});
$("changeBtn").addEventListener("click", () => ($("loader").hidden ? openLoader() : ($("loader").hidden = true)));
$("libCancel").addEventListener("click", () => { $("loader").hidden = true; });
$("libLoad").addEventListener("click", loadSelected);
$("libFilter").addEventListener("input", () => renderLibrary());
$("libAll").addEventListener("change", () => renderLibrary());
$("libList").addEventListener("change", numberChosen);
document.addEventListener("keydown", (e) => {
  if (e.target.closest("input, select, textarea, button, tr")) return;
  if (e.key === " ") { e.preventDefault(); toggle(); }
  else if (e.key === "ArrowRight") $("next").click();
  else if (e.key === "ArrowLeft") prev();
});

init();
