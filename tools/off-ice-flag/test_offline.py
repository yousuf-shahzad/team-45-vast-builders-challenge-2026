"""Offline check for lanes A, B, D against contracts/. No network: W&B and VAST are faked.

    pip install jsonschema httpx fastapi uvicorn
    python test_offline.py
"""

import json
import os
from pathlib import Path

from jsonschema import Draft202012Validator
from referencing import Registry, Resource

os.environ.pop("OFF_ICE_ORIGINAL_VIDEO", None)

import decision
import eval as evaluation
import questions
import vss_client

CONTRACTS = Path(__file__).resolve().parent.parent.parent / "contracts"
FIX = CONTRACTS / "fixtures"
SCHEMAS = {p.stem.split(".")[0]: json.loads(p.read_text()) for p in CONTRACTS.glob("*.schema.json")}
REGISTRY = Registry().with_resources((s["$id"], Resource.from_contents(s)) for s in SCHEMAS.values())


def valid(name, obj):
    errors = list(Draft202012Validator(SCHEMAS[name], registry=REGISTRY).iter_errors(obj))
    assert not errors, f"{name}: {errors[0].message}"


def load(name):
    return json.loads((FIX / name).read_text())


tape, whys, dets, qs = load("tape.json"), load("why.json"), load("detections.json"), load("questions.json")
why_by_id = {w["card_id"]: w for w in whys["items"]}

# --- Lane B: rule, clause, sentence checks ---------------------------------
for card in tape["cards"]:
    assert decision.decide(card["contact"], card["signs"]) == card["decision"], card["id"]
    w = why_by_id[card["id"]]
    assert decision.clause(card["signs"]) == w["clause"]
    assert decision.signs_true(card["signs"]) == w["signs_true"]
    assert decision.sentence_ok(w["sentence"], card)
    valid("card", card)
    valid("why", decision.why_object(card, w["sentence"]))
assert decision.decide(False, {k: True for k in decision.SIGNS}) is None
assert decision.decide(True, {"head_neck": True, "slow_to_rise": False, "left_ice": False, "favoring_limb": True}) == "come_off"
down = tape["cards"][2]
assert not decision.sentence_ok("Come off. The caption says stays down, then gets up slowly and skates to the bench. A concussion.", down)
assert not decision.sentence_ok("Watch. The caption says stays down, then gets up slowly and skates to the bench.", down)
assert not decision.sentence_ok("Come off. Slow to rise.", down)

# extraction: a wrong quote gets one correction turn, then the caption's own text is stored
replies = iter([
    '{"contact": true, "head_neck": false, "slow_to_rise": true, "left_ice": true, "favoring_limb": false, "evidence_quote": "he was hurt"}',
    '```json\n{"contact": true, "head_neck": false, "slow_to_rise": true, "left_ice": true, "favoring_limb": false, "evidence_quote": "Stays down, then gets up slowly and skates to the bench."}\n```',
])
decision._chat = lambda messages: next(replies)
seg = {k: down[k] for k in ("id", "source", "start_sec", "end_sec", "caption")}
card = decision.make_card(seg)
assert card == down, card
decision._chat = lambda m: '{"contact": true, "head_neck": 1, "slow_to_rise": false, "left_ice": false, "favoring_limb": false, "evidence_quote": ""}'
try:
    decision.make_card(seg)
    raise AssertionError("bad signs must raise")
except decision.UpstreamError:
    pass
decision._chat = lambda m: '{"contact": false, "head_neck": false, "slow_to_rise": false, "left_ice": false, "favoring_limb": false, "evidence_quote": ""}'
assert decision.make_card(seg) is None

# why phrasing: good sentences kept, banned / ungrounded ones dropped, the rest of the tape unaffected
bad = dict(why_by_id["seg-head"], sentence="Watch. A possible injury to the head.")
decision._chat = lambda m: json.dumps({"sentences": [{"id": w["card_id"], "sentence": w["sentence"]} for w in whys["items"] if w["card_id"] != "seg-head"] + [{"id": "seg-head", "sentence": bad["sentence"]}]})
got = decision.phrase_whys(tape["cards"])
assert set(got) == {"seg-bump", "seg-down", "seg-bump-2"} and got["seg-down"] == why_by_id["seg-down"]
decision._chat = lambda m: (_ for _ in ()).throw(decision.UpstreamError("down"))
assert decision.phrase_whys(tape["cards"]) == {}

# no medical claims: a quote naming one is corrected, the shown caption is redacted, contradictions dropped
hurt_seg = dict(seg, caption="After a check he stays down with a possible injury, then skates to the bench.")
replies = iter([
    '{"contact": true, "head_neck": false, "slow_to_rise": true, "left_ice": true, "favoring_limb": false, "evidence_quote": "stays down with a possible injury"}',
    '{"contact": true, "head_neck": false, "slow_to_rise": true, "left_ice": true, "favoring_limb": false, "evidence_quote": "skates to the bench"}',
])
decision._chat = lambda m: next(replies)
hurt_card = decision.make_card(hurt_seg)
assert hurt_card["caption"] == "After a check he stays down with a possible [...], then skates to the bench."
assert hurt_card["evidence_quote"] in hurt_card["caption"] and not decision.BANNED.search(hurt_card["caption"])
for word in ("injuries", "medically", "sprained", "concussed", "in pain", "Severely"):
    assert decision.BANNED.search(word), word
for word in ("several", "steering", "paint", "heading"):
    assert not decision.BANNED.search(word), word
assert not decision.sentence_ok("Come off, or play on. The caption says stays down, then gets up slowly and skates to the bench.", down)
assert set(decision.trace(down)) == {"id", "decision", "clause", "signs", "evidence_quote"}  # spec: traces only

# --- Lane D: saved questions reproduce the fixture file -------------------
state = questions.fixture_state()
assert state.questions == qs["questions"], [q["id"] for q in state.questions]
for q in state.questions:
    valid("question", q)

cases = {
    "Who should have come off?": ("decision", ["seg-down"]),
    "what objects are in frame when he stays down?": ("detections", ["seg-down"]),
    "Did anyone favour a leg?": ("decision", []),
    "Why?": ("decision", ["seg-down"]),
    "what happened after the head hit?": ("decision", ["seg-head", "seg-down"]),
    "Summarize the period": ("synthesize", None),
    "How many players are on the ice?": ("agent_ask", None),
    "Which hits did they play-on through": ("decision", ["seg-bump", "seg-bump-2"]),
}
for text, (route, ids) in cases.items():
    assert questions.route_for(text) == route, (text, questions.route_for(text))
    a = questions.ask(state, text)
    valid("question", a)
    assert a["source"] == "live" and a["tools"][0] == {"decision": "rule"}.get(route, route), (text, a)
    if ids is not None:
        assert a["card_ids"] == ids, (text, a["card_ids"])
assert questions.ask(state, "Did anyone favour a leg?")["answer"] == questions.NO_CARD
assert questions.ask(state, "what is in the frame")["answer"].startswith("In frame: person 4.")
assert questions.recap_ok(qs["questions"][-1]["answer"])
assert not questions.recap_ok("Then he should come off the ice.")

# --- Lane D: eval -----------------------------------------------------------
e = evaluation.score(load("eval.json")["labels"], tape["cards"])
valid("eval", e)
assert e["matched"] == 8 and e["misses"] == []
off = [dict(c, decision="watch") if c["id"] == "seg-down" else c for c in tape["cards"]]
assert [m["label_id"] for m in evaluation.score(load("eval.json")["labels"], off)["misses"]] == ["L5"]

# --- Lane A: segment mapping + label counts ----------------------------------
row = {"source": "s3://b/x_seg_0003.mp4", "segment_number": 3, "reasoning_content": " A hit. "}
assert vss_client.map_segment(row, 0) == {"id": "seg-003", "source": "s3://b/x_seg_0003.mp4", "start_sec": 10.0, "end_sec": 15.0, "caption": "A hit."}
sidecar = {"frames": [{"detections": [{"label": "person", "bbox": [1, 2, 3, 4]}, {"class_name": "person"}]},
                      {"detections": [{"label": "person"}, {"label": "sports ball"}]}]}
assert vss_client._label_counts(sidecar) == {"person": 2, "sports ball": 1}
assert vss_client._label_counts({"boxes": [[1, 2, 3, 4]]}) == {}

# --- Lane D: progressive build of a long game, VAST and W&B faked ------------
segs = [{k: c[k] for k in ("id", "source", "start_sec", "end_sec", "caption")} for c in tape["cards"]]
segs.insert(1, {"id": "seg-skate", "source": "s3://x/skate", "start_sec": 25, "end_sec": 30, "caption": "Players skating."})
vss_client.detections = lambda i, s: next(d for d in dets["items"] if d["id"] == i) if i != "seg-head" else {"contract_version": 2, "id": i, "available": False, "objects": []}
vss_client.synthesize = lambda ov, q: (qs["questions"][-1]["answer"], [("s3://x/skate", None, None), (None, 74.0, 80.0)])
by_caption = {c["caption"]: c for c in tape["cards"]}
calls = {"signs": 0, "why": 0}


def fake_chat(messages):
    text = messages[-1]["content"]
    if text.startswith("Traces"):
        calls["why"] += 1
        return json.dumps({"sentences": [{"id": w["card_id"], "sentence": w["sentence"]} for w in whys["items"]]})
    calls["signs"] += 1
    if "FAILS" in text:
        raise decision.UpstreamError("bad reply")
    cap = next((c for c in by_caption if c in text), None)
    if cap is None:
        return '{"contact": false, "head_neck": false, "slow_to_rise": false, "left_ice": false, "favoring_limb": false, "evidence_quote": ""}'
    c = by_caption[cap]
    return json.dumps(dict(c["signs"], contact=True, evidence_quote=c["evidence_quote"]))


decision._chat = fake_chat
ov = "s3://team-45-vss-chunks/team-45/20261017_game.mp4"
b = questions.Builder(ov)
assert b.state.tape["status"] == "indexing" and b.state.cards == []

vss_client.segments = lambda o: ("indexing", segs[:3])  # VAST has indexed the first three so far
assert not b.step()
part = b.state
valid("tape", part.tape)
assert part.tape["status"] == "indexing" and [c["id"] for c in part.cards] == ["seg-bump", "seg-head"]
assert part.recap is None and "clip-recap" not in [q["id"] for q in part.questions]
assert questions.ask(part, "was the head involved?")["card_ids"] == ["seg-head"]
assert questions.ask(part, "who should come off?")["answer"] == questions.NO_CARD

broken = {"id": "seg-bad", "source": "s3://x/bad", "start_sec": 110, "end_sec": 115, "caption": "FAILS to parse."}
vss_client.segments = lambda o: ("ready", segs + [broken])
assert not b.step() and not b.step()  # the bad segment is retried on each poll
assert b.step()                       # third failure: left out, tape complete
done = b.state
valid("tape", done.tape)
assert done.tape["status"] == "ready" and done.tape["video_id"] == "20261017_game"
assert [c["id"] for c in done.cards] == [c["id"] for c in tape["cards"]]
assert calls["signs"] == len(segs) + 3, calls  # each good segment once, the bad one three times
assert done.whys == why_by_id and done.recap[1] == ["seg-down"]
assert [q["id"] for q in done.questions] == [q["id"] for q in qs["questions"]]
for d in done.detections.values():
    valid("detections", d)

calls["why"] = 0
decision.phrase_whys([dict(tape["cards"][0], id=f"seg-{i:03d}") for i in range(45)])
assert calls["why"] == 3  # 20 cards per W&B call

vss_client.segments = lambda o: ("missing_clip", [])
missing = questions.Builder("s3://x/none.mp4")
assert not missing.step() and missing.state.tape["status"] == "missing_clip"

# --- Lane D: HTTP routes ----------------------------------------------------
from fastapi.testclient import TestClient

import main

with TestClient(main.app) as client:
    r = client.get("/api/tape")
    assert r.status_code == 200 and r.headers["X-Off-Ice-Fixture"] == "1"
    valid("tape", r.json())
    assert client.get("/health").json() == {"ok": True}
    assert client.get("/api/questions").json()["questions"] == qs["questions"]
    valid("why", client.get("/api/cards/seg-down/why").json())
    valid("detections", client.get("/api/segments/seg-down/detections").json())
    assert client.get("/api/cards/nope/why").status_code == 404
    assert client.get("/api/segments/nope/detections").status_code == 404
    assert client.get("/api/segments/seg-down/stream").status_code == 404
    assert client.post("/api/ask", json={"question": "  "}).status_code == 400
    assert client.post("/api/ask", content=b"not json").status_code == 400
    a = client.post("/api/ask", json={"question": "Who should have come off?"}).json()
    assert a["card_ids"] == ["seg-down"] and a["source"] == "live"
    assert client.get("/api/eval").json()["matched"] == 8

    # a real game, mid-indexing: routes answer from the cards built so far
    os.environ["OFF_ICE_ORIGINAL_VIDEO"] = ov
    questions._builder, b.state = b, part
    r = client.get("/api/tape")
    assert r.json()["status"] == "indexing" and len(r.json()["cards"]) == 2 and "X-Off-Ice-Fixture" not in r.headers
    assert client.get("/api/cards/seg-head/why").status_code == 200
    assert client.get("/api/cards/seg-down/why").status_code == 404  # not indexed yet
    assert client.get("/api/eval").status_code == 409
    b.state = missing.state
    r = client.post("/api/ask", json={"question": "why"})
    assert r.status_code == 409 and r.json() == {"error": "not_ready", "status": "missing_clip"}
    assert client.get("/api/segments/seg-head/stream").status_code == 409

print("ok: lanes A, B, D pass against contracts/fixtures")
