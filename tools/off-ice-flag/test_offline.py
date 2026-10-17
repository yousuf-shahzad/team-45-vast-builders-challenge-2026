"""Offline check for lanes A, B, D against contracts/. No network: W&B and VAST are faked.

    pip install jsonschema httpx fastapi uvicorn
    python test_offline.py
"""

import json
import os
import re
from pathlib import Path

from jsonschema import Draft202012Validator
from referencing import Registry, Resource

os.environ.pop("OFF_ICE_ORIGINAL_VIDEO", None)
os.environ.pop("OFF_ICE_CHUNKS", None)

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
# a goalie down for a save: the model may tick slow_to_rise, but no contact means no sign and no card
decision._chat = lambda m: '{"contact": false, "head_neck": false, "slow_to_rise": true, "left_ice": false, "favoring_limb": false, "evidence_quote": "stays down"}'
assert decision.extract_signs("The goalie stays down in the crease after the save.") == dict(
    contact=False, head_neck=False, slow_to_rise=False, left_ice=False, favoring_limb=False, evidence_quote="")
prompt = (CONTRACTS / "ingest_prompt.txt").read_text(encoding="utf-8").strip()
assert len(prompt) <= 800 and "No body contact." in prompt  # 800 is the API's custom_prompt cap
assert not decision.BANNED.search(prompt.replace("Do not name an injury or its severity.", ""))
assert len(decision.CHECKS) >= 6 and sum(want is None for _, want in decision.CHECKS) >= 4

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

# --- Lane D: search ----------------------------------------------------------
assert questions.search(state, "hits into the boards")["card_ids"] == ["seg-bump"]
assert questions.search(state, "stays down then skates to the bench")["card_ids"][0] == "seg-down"

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
def fake_detections(i, source):
    base = re.sub(r"^p\d+-", "", i)
    if base == "seg-head":
        return {"contract_version": 2, "id": i, "available": False, "objects": []}
    return dict(next(d for d in dets["items"] if d["id"] == base), id=i)


vss_client.detections = fake_detections
vss_client.synthesize = lambda ov, q: (qs["questions"][-1]["answer"], [("s3://x/skate", None, None), (None, 74.0, 80.0)])
by_caption = {c["caption"]: c for c in tape["cards"]}
calls = {"signs": 0, "why": 0}


def fake_chat(messages):
    text = messages[-1]["content"]
    if text.startswith("Traces"):
        calls["why"] += 1
        ids = re.findall(r'"id": "([^"]+)"', text)  # answer for exactly the cards asked about
        return json.dumps({"sentences": [{"id": i, "sentence": why_by_id[re.sub(r"^p\d+-", "", i)]["sentence"]}
                                         for i in ids if re.sub(r"^p\d+-", "", i) in why_by_id]})
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
b = questions.Builder([ov])
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
assert done.recap[0] == qs["questions"][-1]["answer"]  # one part: no clock prefix
assert [q["id"] for q in done.questions] == [q["id"] for q in qs["questions"]]
for d in done.detections.values():
    valid("detections", d)

calls["why"] = 0
decision.phrase_whys([dict(tape["cards"][0], id=f"seg-{i:03d}") for i in range(45)])
assert calls["why"] == 3  # 20 cards per W&B call

vss_client.segments = lambda o: ("missing_clip", [])
missing = questions.Builder(["s3://x/none.mp4"])
assert not missing.step() and missing.state.tape["status"] == "missing_clip"

# --- Lane D: a pre-split game: two parts stitched onto one clock --------------
# Part 1 runs 0-60 s; part 2's clock starts at 0 again, so its cards shift by 60 s and land on
# the fixture times (75 s, 100 s).
ov1 = "s3://team-45-vss-chunks/team-45/20261017_1000_game_part_000.mp4"
ov2 = "s3://team-45-vss-chunks/team-45/20261017_1001_game_part_001.mp4"
fixture_seg = {c["id"]: {k: c[k] for k in ("id", "source", "start_sec", "end_sec", "caption")} for c in tape["cards"]}
part1 = [fixture_seg["seg-bump"], fixture_seg["seg-head"],
         {"id": "seg-quiet", "source": "s3://x/quiet", "start_sec": 55, "end_sec": 60, "caption": "Players skating."}]
part2 = [dict(fixture_seg["seg-down"], start_sec=15, end_sec=22), dict(fixture_seg["seg-bump-2"], start_sec=40, end_sec=45)]
seg_calls = []
part_state = {ov1: ("ready", part1), ov2: ("indexing", part2[:1])}
vss_client.segments = lambda o: seg_calls.append(o) or part_state[o]
vss_client.synthesize = lambda o, q: ({ov1: "Shoulder contact along the boards, then a hit reaches the head.",
                                        ov2: "A player stays down and skates to the bench."}[o], [(None, 14.0, 20.0)] if o == ov2 else [])
calls["signs"] = 0
mb = questions.Builder([ov1, ov2])
assert not mb.step()
assert mb.state.tape["status"] == "indexing"
assert [c["id"] for c in mb.state.cards] == ["p01-seg-bump", "p01-seg-head", "p02-seg-down"]
part_state[ov2] = ("ready", part2)
assert mb.step()
game = mb.state
valid("tape", game.tape)
assert game.tape["video_id"] == "20261017_1000_game" and game.tape["duration_sec"] == 105
assert [(c["id"], c["start_sec"], c["end_sec"]) for c in game.cards] == [
    ("p01-seg-bump", 12, 17), ("p01-seg-head", 40, 45), ("p02-seg-down", 75, 82), ("p02-seg-bump-2", 100, 105)]
assert game.cards[2]["stream_path"] == "/api/segments/p02-seg-down/stream"
assert [c["decision"] for c in game.cards] == [c["decision"] for c in tape["cards"]]
assert calls["signs"] == 5  # every segment once, across both polls
assert seg_calls.count(ov1) == 1  # a fully indexed part is not polled again
assert set(game.whys) == {c["id"] for c in game.cards}
assert game.parts == [(ov1, 0, 60), (ov2, 60, 45)]
assert game.recap[0].startswith("0:00–1:00: Shoulder contact") and "1:00–1:45: A player stays down" in game.recap[0]
assert game.recap[1] == ["p01-seg-bump", "p01-seg-head", "p02-seg-down"]  # part 1 had no spans: its whole range
assert next(q for q in game.questions if q["id"] == "who-comes-off")["card_ids"] == ["p02-seg-down"]
vss_client.agent_ask = lambda q, o: ("He goes down along the boards." if o == ov2 else "A diagnosis is unclear.",
                                     [(None, 16.0, 18.0)])
live = questions.ask(game, "How many players are on the ice?")
valid("question", live)
assert live["answer"] == "1:00–1:45: He goes down along the boards." and live["card_ids"] == ["p02-seg-down"]
assert live["tools"] == ["agent_ask"]  # part 1's answer failed the word check and was dropped

# a pass that dies after building cards is finished on the next poll, not left without whys
real_phrase, flaky = decision.phrase_whys, {"n": 0}


def flaky_phrase(cards):
    flaky["n"] += 1
    if flaky["n"] == 1:
        raise RuntimeError("crash mid-pass")
    return real_phrase(cards)


decision.phrase_whys = flaky_phrase
vss_client.segments = lambda o: ("ready", segs)
vss_client.synthesize = lambda o, q: (qs["questions"][-1]["answer"], [])
fb = questions.Builder(["s3://x/flaky.mp4"])
try:
    fb.step()
    raise AssertionError("the crash should surface to run(), which logs and polls again")
except RuntimeError:
    pass
assert not fb.whys
assert fb.step() and fb.whys == why_by_id and set(fb.dets) == {c["id"] for c in tape["cards"]}
decision.phrase_whys = real_phrase

# --- Lane A: library from Explore ----------------------------------------------
pages = {0: {"total": 101, "videos": [{"original_video": ov1, "filename": "game_part_000.mp4", "timeline": [1, 2],
                                       "camera_id": "rink_cam-1"}] + [{"original_video": "s3://x/lab", "filename": "lab.mp4"}] * 99},
         100: {"total": 101, "videos": [{"original_video": ov2, "filename": "game_part_001.mp4", "metadata": {"location": "rink"}}]}}
real_json = vss_client._json
vss_client._json = lambda path, **kw: pages[kw["params"]["offset"]]
lib = vss_client.library()
vss_client._json = real_json
assert len(lib) == 101 and lib[0] == {"original_video": ov1, "filename": "game_part_000.mp4", "clips": 2, "ours": True}
assert lib[-1]["ours"] and not lib[1]["ours"]

# --- Lane D: HTTP routes ----------------------------------------------------
from fastapi.testclient import TestClient

import main

with TestClient(main.app) as client:
    r = client.get("/api/tape")
    assert r.status_code == 200 and r.headers["X-Off-Ice-Fixture"] == "1"
    valid("tape", r.json())
    assert client.get("/api/tape", params={"source": "whole"}).json() == r.json()  # every tab shows the sample
    assert client.get("/api/sources").json() == {"default": "chunks", "fixture": True, "sources": {"chunks": [], "whole": []}}
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
    assert client.get("/api/search", params={"q": "boards"}).json() == {"query": "boards", "card_ids": ["seg-bump"]}
    assert client.get("/api/search", params={"q": " "}).status_code == 400

    # loading: bad input is 400 and changes nothing
    assert client.post("/api/sources/whole", json={"videos": [ov1, ov2]}).status_code == 400
    assert client.post("/api/sources/chunks", json={"videos": ["not-s3"]}).status_code == 400
    assert client.post("/api/sources/bogus", json={"videos": [ov1]}).status_code == 400
    assert client.post("/api/sources/chunks", content=b"nope").status_code == 400
    assert questions.is_fixture()
    vss_client.library = lambda: lib[:1]
    assert client.get("/api/library").json() == {"videos": lib[:1]}

    # Split game tab: the stitched game. Loading drops the tab's builder (a reload rebuilds, e.g. after
    # a re-ingest), so the prebuilt one goes in after the load and no thread starts.
    questions._builders["chunks"] = sentinel = questions.Builder([ov1, ov2])
    s = client.post("/api/sources/chunks", json={"videos": [ov1, ov2]}).json()
    assert s == {"default": "chunks", "fixture": False, "sources": {"chunks": [ov1, ov2], "whole": []}}
    assert "chunks" not in questions._builders and sentinel.stopped
    questions._builders["chunks"] = mb
    r = client.get("/api/tape", params={"source": "chunks"})
    assert "X-Off-Ice-Fixture" not in r.headers and [c["id"] for c in r.json()["cards"]][2] == "p02-seg-down"
    assert client.get("/api/tape").json() == r.json()  # chunks is the default tab
    assert client.get("/api/cards/p02-seg-down/why", params={"source": "chunks"}).status_code == 200
    a = client.post("/api/ask", params={"source": "chunks"}, json={"question": "Who should have come off?"}).json()
    assert a["card_ids"] == ["p02-seg-down"]

    # Whole video tab: nothing loaded yet, then a game mid-indexing
    assert client.get("/api/tape", params={"source": "whole"}).json()["status"] == "missing_clip"
    r = client.post("/api/ask", params={"source": "whole"}, json={"question": "why"})
    assert r.status_code == 409 and r.json() == {"error": "not_ready", "status": "missing_clip"}
    client.post("/api/sources/whole", json={"videos": [ov]})
    questions._builders["whole"], b.state = b, part
    r = client.get("/api/tape", params={"source": "whole"})
    assert r.json()["status"] == "indexing" and len(r.json()["cards"]) == 2
    assert client.get("/api/cards/seg-head/why", params={"source": "whole"}).status_code == 200
    assert client.get("/api/cards/seg-down/why", params={"source": "whole"}).status_code == 404  # not indexed yet
    assert client.get("/api/eval", params={"source": "whole"}).status_code == 409
    vss_client.search = lambda q: ["s3://elsewhere/x", segs[2]["source"], segs[0]["source"], segs[0]["source"]]
    assert client.get("/api/search", params={"q": "head hit", "source": "whole"}).json()["card_ids"] == ["seg-head", "seg-bump"]
    b.state = missing.state
    assert client.get("/api/segments/seg-head/stream", params={"source": "whole"}).status_code == 409

print("ok: lanes A, B, D pass against contracts/fixtures")
