"""Lane D: tape build + cache, saved questions, and the live ask router (contract version 2).

Calls vss_client (VAST) and decision (W&B). Opens no HTTP client of its own.
"""

import json
import os
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

import decision
import vss_client

HERE = Path(__file__).resolve().parent
FIXTURES = HERE / "fixtures" if (HERE / "fixtures").is_dir() else HERE.parent.parent / "contracts" / "fixtures"
MAX_ANSWER = 1200
NO_CARD = "No card on this clip matches that."
NO_DETECTIONS = "No stored detections for that moment."


class NotReady(Exception):
    def __init__(self, status):
        super().__init__(status)
        self.status = status


class Upstream(Exception):
    """A model or VAST answer failed the checks. Routes map this to 502."""


@dataclass
class State:
    tape: dict
    fixture: bool = False
    whys: dict = field(default_factory=dict)        # card id -> why object
    detections: dict = field(default_factory=dict)  # card id -> detections object
    questions: list = field(default_factory=list)
    recap: tuple = None                             # (answer, card_ids)

    @property
    def ready(self):
        return self.tape["status"] == "ready"

    @property
    def cards(self):
        return self.tape["cards"]

    def card(self, card_id):
        if not self.ready:
            raise NotReady(self.tape["status"])
        for c in self.cards:
            if c["id"] == card_id:
                return c
        return None


# --- text matching -----------------------------------------------------------

def _word(w):
    return rf"(?<![a-z]){w}(?![a-z])"


def _norm(text):
    return text.lower().replace("’", "'")


COME_OFF = r"come[ _-]off"
PLAY_ON = r"play[ _-]on|play(ed)? through"
SIGN_WORDS = {
    "head_neck": _word("(head|neck)"),
    "slow_to_rise": _word("slow") + "|" + _word("stays? down"),
    "left_ice": _word("bench") + "|" + _word("(leaves|left) the ice"),
    "favoring_limb": r"(?<![a-z])favou?r",
}
DETECTION_PHRASES = ("in frame", "in the frame", "detected", "detection", "bounding", "what objects",
                     "what's in", "what is in")
DECISION_PHRASES = ("come off", "come-off", "come_off", "play on", "play-on", "play_on", "play through",
                    "played through", "leaves the ice", "left the ice", "skates to the bench", "slow to")
DECISION_WHOLE = ("why", "watch", "bench", "head", "neck", "sign", "signs", "favor", "favour", "favoring",
                  "favouring")
SYNTH_PHRASES = ("summar", "recap", "what happens in this clip", "what happens in the clip",
                 "what happened in this clip", "what happened in the clip")
RECAP_DECISION = re.compile(_word("(come off|watch|play on)"), re.I)


def route_for(text):
    t = _norm(text)
    if any(p in t for p in DETECTION_PHRASES):
        return "detections"
    if any(p in t for p in DECISION_PHRASES) or any(re.search(_word(w), t) for w in DECISION_WHOLE):
        return "decision"
    if any(p in t for p in SYNTH_PHRASES):
        return "synthesize"
    return "agent_ask"


def select_cards(text, cards):
    """The spec's card filter, in start_sec order."""
    t = _norm(text)
    if re.search(COME_OFF, t):
        sel = [c for c in cards if c["decision"] == "come_off"]
    elif re.search(PLAY_ON, t):
        sel = [c for c in cards if c["decision"] == "play_on"]
    elif re.search(_word("watch"), t):
        sel = [c for c in cards if c["decision"] == "watch"]
    else:
        signs = [k for k, p in SIGN_WORDS.items() if re.search(p, t)]
        if signs:
            sel = [c for c in cards if any(c["signs"][k] for k in signs)]
        elif re.search(_word("why"), t):
            sel = [c for c in cards if c["decision"] == "come_off"] or list(cards)
        else:
            sel = list(cards)
    ids = {c["id"] for c in sel}
    pos = {c["id"]: i for i, c in enumerate(cards)}
    for word, step in (("before", -1), ("after", 1)):
        if re.search(_word(word), t):
            for cid in list(ids):
                j = pos[cid] + step
                if 0 <= j < len(cards):
                    ids.add(cards[j]["id"])
    return [c for c in cards if c["id"] in ids]


def overlap_ids(cards, spans):
    """Card ids touched by (source, start, end) spans, in start_sec order."""
    ids = set()
    for source, start, end in spans:
        for c in cards:
            if source and source == c.get("source"):
                ids.add(c["id"])
            elif start is not None and end is not None and start < c["end_sec"] and end > c["start_sec"]:
                ids.add(c["id"])
    return [c["id"] for c in cards if c["id"] in ids]


def _fit(text):
    """Trim free text to the schema cap at a sentence end."""
    if len(text) <= MAX_ANSWER:
        return text
    cut = text[:MAX_ANSWER]
    return cut[:cut.rfind(". ") + 1] if ". " in cut else cut


def _q(qid, question, answer, card_ids, source, grounding, tools):
    return {"contract_version": 2, "id": qid, "question": question, "answer": answer, "card_ids": card_ids,
            "source": source, "grounding": grounding, "tools": tools}


# --- answers -----------------------------------------------------------------

def decision_answer(state, text, qid, source):
    """Cached why sentences for the selected cards. None when cards match but none has a sentence."""
    sel = select_cards(text, state.cards)
    if not sel:
        return _q(qid, text, NO_CARD, [], source, "decision", ["rule"])
    parts, ids = [], []
    for c in sel:
        why = state.whys.get(c["id"])
        if why and len(" ".join(parts + [why["sentence"]])) <= MAX_ANSWER:
            parts.append(why["sentence"])
            ids.append(c["id"])
    if not parts:
        return None
    return _q(qid, text, " ".join(parts), ids, source, "decision", ["rule", "wandb"])


def detections_answer(state, text):
    lines, ids = [], []
    for c in select_cards(text, state.cards):
        d = state.detections.get(c["id"])
        if d and d["available"]:
            lines.append("In frame: " + ", ".join(f"{o['label']} {o['count']}" for o in d["objects"]) + ".")
            ids.append(c["id"])
    return _q("live", text, " ".join(lines) or NO_DETECTIONS, ids, "live", "clip", ["detections"])


def recap_ok(answer):
    return bool(answer) and not decision.BANNED.search(answer) and not RECAP_DECISION.search(answer)


def _recap(state, original_video, question):
    answer, spans = vss_client.synthesize(original_video, question)
    if not recap_ok(answer):
        raise Upstream("recap failed the word checks")
    return _fit(answer), overlap_ids(state.cards, spans) if spans else [c["id"] for c in state.cards]


SAVED = (
    ("who-comes-off", "Who should have come off?"),
    ("why-come-off", "Why is that a come off?"),
    ("head-involved", "Was the head involved in any contact?"),
    ("played-through", "Which hits did they play through?"),
    ("before-come-off", "What happened just before the come-off?"),
)


def saved_questions(state):
    out = [q for qid, text in SAVED if (q := decision_answer(state, text, qid, "saved"))]
    if state.recap:
        answer, ids = state.recap
        out.append(_q("clip-recap", "What happens in this clip?", answer, ids, "saved", "clip", ["synthesize"]))
    return out


def ask(state, text):
    """POST /api/ask. Raises ValueError (400), NotReady (409), Upstream (502)."""
    text = (text or "").strip()
    if not text:
        raise ValueError("empty question")
    if not state.ready:
        raise NotReady(state.tape["status"])
    route = route_for(text)
    if route == "detections":
        return detections_answer(state, text)
    if route == "decision":
        q = decision_answer(state, text, "live", "live")
        if q is None:
            raise Upstream("matching cards have no cached why sentence")
        return q
    if state.fixture:  # no VAST call; the header marks the body as a fixture
        answer, ids = state.recap
        return _q("live", text, answer, ids, "live", "clip", [route])
    ov = state.tape["original_video"]
    if route == "synthesize":
        answer, ids = _recap(state, ov, text)
        return _q("live", text, answer, ids, "live", "clip", ["synthesize"])
    answer, spans = vss_client.agent_ask(text, ov)
    if decision.BANNED.search(answer):
        raise Upstream("agent answer failed the word check")
    return _q("live", text, _fit(answer), overlap_ids(state.cards, spans), "live", "clip", ["agent_ask"])


# --- state -------------------------------------------------------------------

def _load(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def fixture_state():
    tape = _load("tape.json")
    state = State(tape=tape, fixture=True,
                  whys={w["card_id"]: w for w in _load("why.json")["items"]},
                  detections={d["id"]: d for d in _load("detections.json")["items"]})
    recap = next(q for q in _load("questions.json")["questions"] if q["id"] == "clip-recap")
    state.recap = (recap["answer"], recap["card_ids"])
    state.questions = saved_questions(state)
    return state


_card_cache = {}  # (segment id, caption) -> card or None; a rebuild only retries failures


def _card(seg):
    key = (seg["id"], seg["caption"])
    if key not in _card_cache:
        _card_cache[key] = decision.make_card(seg)
    return _card_cache[key]


def build_state(original_video):
    status, segments = vss_client.segments(original_video)
    tape = {"contract_version": 2, "status": status, "video_id": Path(original_video).stem,
            "original_video": original_video, "cards": []}
    if status != "ready":
        return State(tape=tape)
    with ThreadPoolExecutor(max_workers=8) as pool:
        cards = [c for c in pool.map(_card, segments) if c]  # any failed extraction raises -> 502
        tape["cards"] = sorted(cards, key=lambda c: c["start_sec"])
        tape["duration_sec"] = max((s["end_sec"] for s in segments), default=0)
        state = State(tape=tape, whys=decision.phrase_whys(tape["cards"]))

        def det(c):
            try:
                return vss_client.detections(c["id"], c["source"])
            except vss_client.UpstreamError:
                return None  # not cached -> that route answers 502, the card stays
        state.detections = {d["id"]: d for d in pool.map(det, tape["cards"]) if d}
    try:
        state.recap = _recap(state, original_video, "What happens in this clip?")
    except (vss_client.UpstreamError, Upstream):
        state.recap = None
    state.questions = saved_questions(state)
    return state


_lock = threading.Lock()
_state = None


def current_state():
    """Fixtures until OFF_ICE_ORIGINAL_VIDEO is set. A ready tape is built once and cached."""
    global _state
    with _lock:
        ov = os.environ.get("OFF_ICE_ORIGINAL_VIDEO", "").strip()
        if _state is None or not _state.ready or _state.fixture != (not ov):
            _state = build_state(ov) if ov else fixture_state()
        return _state
