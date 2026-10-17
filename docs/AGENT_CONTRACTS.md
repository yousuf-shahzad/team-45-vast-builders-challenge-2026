# Agent contracts

Contract version **2**. Four people, four Cursor sessions, all Grok 4.7, one clone of this repo. The shared surface is the schemas and the fixtures. It is not a shared scratch file.

Read [OFF_ICE_FLAG_SPEC.md](OFF_ICE_FLAG_SPEC.md) before editing. The Cursor rule [.cursor/rules/off-ice-flag.mdc](../.cursor/rules/off-ice-flag.mdc) applies in every session.

Version 2 adds the why sentence, stored detector labels, and the question router. The decision rule is unchanged. `contract_version` on every schema and fixture is 2.

## How to work at the same time

1. Pull `yousuf` before you start a lane.
2. Touch only the paths your lane owns.
3. Build and test against [contracts/fixtures/](../contracts/fixtures/) until a real `original_video` exists. The tape, why, detections, and questions fixtures describe the same four cards.
4. Validate every JSON you emit against the schema for that object before you call the lane done.
5. `tools/off-ice-flag/main.py` only wires routers. Lane D owns it. Other lanes import nothing from it and do not edit it.
6. `vss_client.py` and `decision.py` do not import each other. `questions.py` and `main.py` may call both.
7. A schema change is a stop. Bump `contract_version` in the same commit, say so in the message, and wait until the other three lanes have pulled.

Do not commit passwords, JWTs, `WANDB_API_KEY`, or `/config/team-45.config`.

Remote for this team: `yousuf` → `git@github.com:yousuf-shahzad/vast-builders-challenge-2026.git`. Do not push `origin` (that is the workshop repo).

## Lanes

### Lane A — Index

Owns:

- `tools/off-ice-flag/vss_client.py`
- `tools/off-ice-flag/ingest_prompt.txt` (copy of [contracts/ingest_prompt.txt](../contracts/ingest_prompt.txt); if they differ, the contracts file wins)

Does:

- Log in to VAST with the retrieval login skill. Never log the token.
- Upload is a confirmed step, not an automatic one. One file per game; VAST segments it. The file must be under the live upload cap (`max_upload_size_mb`); over it, print the re-encode command. Send `scenario=nhl` and `custom_prompt` from the contracts file.
- Return the segments indexed so far for that `original_video`, sorted by start time, with the status (`ready`, `indexing`, `missing_clip`).
- Map a segment to the fields Lane B needs: `id`, `source`, `start_sec`, `end_sec`, `caption`.
- Proxy `GET /api/segments/{id}/stream` to the VSS stream URL. The JWT stays on the server.
- Read stored YOLO labels for one segment via `GET /api/v1/videos/detections?source=` (videos skill). Return the detections schema: label counts, or `available: false`. Do not invent a label. Do not return box coordinates.
- `POST /api/v1/videos/synthesize` for this `original_video`, with the system prompt in the spec.
- `POST /api/v1/agent/ask` with `original_video` set to this clip. Return the answer and the evidence segments so Lane D can fill `card_ids`.
- `POST /api/v1/search` with the upload tag `off-ice-flag`. Return segment sources, best first.
- Every VAST HTTP call for this app goes through this module. Do not call `POST /api/v1/agent/search-and-answer`. Do not call a GPU host.

Skills: `.cursor/skills/retrieval/login`, `.cursor/skills/retrieval/videos`, `.cursor/skills/retrieval/agent-qa`, `.cursor/skills/ingest/upload-video`.

Paste this into Lane A's session:

```text
You are Lane A on Off-ice Flag, contract version 2. Read docs/OFF_ICE_FLAG_SPEC.md and docs/AGENT_CONTRACTS.md. Edit only tools/off-ice-flag/vss_client.py. Use the retrieval, videos, agent-qa, and upload skills. Own every VAST HTTP call: login, upload, segments, stream proxy, detections, synthesize, and agent ask. Do not call search-and-answer or a GPU host. Do not change schemas. Test against contracts/fixtures until a real clip is indexed. Do not print secrets.
```

### Lane B — Decision

Owns:

- `tools/off-ice-flag/decision.py`

Does:

- Input: one caption string plus the segment timing fields from Lane A.
- Call Weights & Biases chat completions. Base URL defaults to `https://api.inference.wandb.ai/v1`. Override with `WANDB_INFERENCE_BASE`. Auth header `Authorization: Bearer` from `WANDB_API_KEY`.
- On first run, `GET /v1/models` and pin the chosen id in a constant `MODEL_ID` inside `decision.py`. The why sentence uses that same id. Do not rotate models during the demo.
- Temperature 0. Ask for a JSON object with `contact`, the four signs, and `evidence_quote`. Reject the reply if `evidence_quote` is not contained in the caption, or if the JSON does not match the sign fields.
- Apply the decision rule from the spec in Python. Return one card object, `contract_version` 2, including `stream_path` of `/api/segments/{id}/stream`.
- A failed sign extraction raises. It does not invent a `come_off`.
- From a finished card, compute `clause` and `signs_true` with the priority table in the spec.
- For each batch of new cards, one chat completion per 20 cards phrases the why sentences. Send traces only (id, decision, clause, signs, `evidence_quote`). Temperature 0. The reply shape is `{ "sentences": [ { "id", "sentence" } ] }`. Accept a sentence only when it passes the checks in the spec. A rejected sentence drops that why. It does not drop the card and it does not invent a sentence.
- This module owns every Weights & Biases HTTP call. It does not import `vss_client.py`.

Paste this into Lane B's session:

```text
You are Lane B on Off-ice Flag, contract version 2. Read docs/OFF_ICE_FLAG_SPEC.md and docs/AGENT_CONTRACTS.md. Edit only tools/off-ice-flag/decision.py. Signs come from Weights & Biases. The come_off / watch / play_on word comes from the Python rule in the spec. The why sentence is a second Weights & Biases call over the traces, temperature 0, and Python rejects a sentence that fails the spec checks. Validate cards against contracts/card.schema.json and why objects against contracts/why.schema.json. Use contracts/fixtures/tape.json and contracts/fixtures/why.json as tests. Do not print secrets.
```

### Lane C — Replay UI

Owns:

- `tools/off-ice-flag/static/` (the HTML, CSS, and JS)

Does:

- One page. Fetch `GET /api/tape`. Show `status`. While it is `indexing`, render the cards that are there and poll the tape about every 15 seconds for new ones. On `missing_clip`, show the status and no cards. Never invent cards.
- Play `stream_path` in `start_sec` order. Play on and watch continue. Come off pauses.
- Render the clip, the four yes/no signs, `Visible signs n of 4` (counted from `signs`, labelled as a count, never as severity), the decision, and the caption.
- For the card on screen, fetch `GET /api/cards/{id}/why` and `GET /api/segments/{id}/detections`. Show the why sentence under the caption. Show one in-frame line from `objects` (`In frame: person 2`). If either response is not 200, or `available` is false, omit that line. The card stays.
- Question list from `GET /api/questions`. Clicking an answer seeks to the first `card_ids` entry. If `card_ids` is empty, leave the playhead. A text box `POST`s `/api/ask`. Under the answer, one line with `grounding` and the `tools` names.
- Words on screen: Come off, Watch, Play on. Do not write injury, concussion, severity, or any word on the banned list in the spec. Do not draw boxes on the video.

Paste this into Lane C's session:

```text
You are Lane C on Off-ice Flag, contract version 2. Read docs/OFF_ICE_FLAG_SPEC.md and docs/AGENT_CONTRACTS.md. Edit only tools/off-ice-flag/static/. The page plays GET /api/tape, polls it while status is indexing, and pauses on come_off. Cards show signs, decision, caption, the why sentence, and the in-frame line. Questions come from GET /api/questions and POST /api/ask. Use the fixtures as the sample payloads. No build step and no medical wording.
```

### Lane D — Questions, eval, wiring, deploy

Owns:

- `tools/off-ice-flag/questions.py`
- `tools/off-ice-flag/eval.py`
- `tools/off-ice-flag/main.py`
- `tools/off-ice-flag/requirements.txt`
- `tools/off-ice-flag/deploy/` (Kubernetes manifest only)
- `docs/EVAL.md` (the filled-in match count once the clip is labeled)

Does:

- `main.py` creates the FastAPI app, serves `static/`, and mounts the routes from the spec. It does not contain the decision rule, the question router, or the VAST client logic. It calls Lane A and Lane B.
- `questions.py` owns the router in the spec. Decision traces and the why sentence come from Lane B. Agent ask, synthesize, and detections come from Lane A. This file does not open its own HTTP client to VAST or Weights & Biases.
- Precompute the six saved questions when the tape is built. Decision answers are the cached why sentences joined in `start_sec` order. The clip recap is one synthesize call. `POST /api/ask` follows the router, reuses that cache for decision and detection questions, and returns `source: "live"`.
- `GET /api/eval` compares eight hand labels to the tape using the rule in `docs/EVAL.md`. Why, detections, and answers are outside that score.
- Deploy with `.cursor/skills/deployment/deploy-app-no-registry`. App name `off-ice-flag`. People open the workshop **App** button.
- `requirements.txt` pins `fastapi` and `uvicorn`. Keep the tree small enough for a ConfigMap (about 1 MiB).

Paste this into Lane D's session:

```text
You are Lane D on Off-ice Flag, contract version 2. Read docs/OFF_ICE_FLAG_SPEC.md and docs/AGENT_CONTRACTS.md. You own questions.py, eval.py, main.py, requirements.txt, deploy/, and docs/EVAL.md. main.py only wires routes and serves static/. questions.py routes ask, why, and detections by calling decision.py and vss_client.py. Do not edit vss_client.py, decision.py, or static/. Follow the deploy-app-no-registry skill. Do not print secrets.
```

## Route ownership inside main.py

Lane D registers these and they must keep these shapes:

| Route | Implementation |
|---|---|
| `GET /health` | Lane D |
| `GET /api/tape` | The latest snapshot from Lane D's background builder: Lane A segments, Lane B cards and why sentences, Lane A detections, in batches, sorted by `start_sec` |
| `GET /api/segments/{id}/stream` | Lane A via `vss_client` |
| `GET /api/segments/{id}/detections` | Cached detections schema. Lane A fetched it at tape build |
| `GET /api/cards/{id}/why` | Cached why schema. Lane B phrased it at tape build |
| `GET /api/questions` | Lane D |
| `POST /api/ask` | Lane D router in `questions.py` |
| `GET /api/search` | Lane D maps Lane A's search hits to cards by source |
| `GET /api/eval` | Lane D |

Until `OFF_ICE_ORIGINAL_VIDEO` is set, these may return the fixtures: tape (`video_id` `fixture-period`), why, detections, and questions. Set `X-Off-Ice-Fixture: 1` when that happens. Live ask against the fixture does not call VAST or Weights & Biases. Do not present fixture cards as the judged clip.

## Changing a contract

1. Edit the schema and every fixture in one commit.
2. Set `contract_version` to the next integer in the schema `const`, the fixtures, and the top of both docs.
3. Commit message starts with `contract:`.
4. Other lanes stop until they have pulled that commit.

Allowed without a bump: wording inside `docs/OFF_ICE_FLAG_SPEC.md` that does not change a field name, a route, the decision rule, or the question router. The decision rule change is a contract bump. The question router change is a contract bump.
