# Agent contracts

Contract version **1**. Four people, four Cursor sessions, all Grok 4.7, one clone of this repo. The shared surface is the schemas and the fixtures. It is not a shared scratch file.

Read [OFF_ICE_FLAG_SPEC.md](OFF_ICE_FLAG_SPEC.md) before editing. The Cursor rule [.cursor/rules/off-ice-flag.mdc](../.cursor/rules/off-ice-flag.mdc) applies in every session.

## How to work at the same time

1. Pull `yousuf` before you start a lane.
2. Touch only the paths your lane owns.
3. Build and test against [contracts/fixtures/tape.json](../contracts/fixtures/tape.json) until a real `original_video` exists.
4. Validate every JSON you emit against the schema for that object before you call the lane done.
5. `tools/off-ice-flag/main.py` only wires routers. Lane D owns it. Other lanes import nothing from it and do not edit it.
6. A schema change is a stop. Bump `contract_version` in the same commit, say so in the message, and wait until the other three lanes have pulled.

Do not commit passwords, JWTs, `WANDB_API_KEY`, or `/config/team-45.config`.

Remote for this team: `yousuf` → `git@github.com:yousuf-shahzad/vast-builders-challenge-2026.git`. Do not push `origin` (that is the workshop repo).

## Lanes

### Lane A — Index

Owns:

- `tools/off-ice-flag/vss_client.py`
- `tools/off-ice-flag/ingest_prompt.txt` (copy of [contracts/ingest_prompt.txt](../contracts/ingest_prompt.txt); if they differ, the contracts file wins)

Does:

- Log in to VAST with the retrieval login skill. Never log the token.
- Upload is a confirmed step, not an automatic one. The file must be under 100 MB. Send `scenario=nhl` and `custom_prompt` from the contracts file.
- Poll until that `original_video` has indexed segments. Return them sorted by start time.
- Map a segment to the fields Lane B needs: `id`, `source`, `start_sec`, `end_sec`, `caption`.
- Proxy `GET /api/segments/{id}/stream` to the VSS stream URL. The JWT stays on the server.

Skills: `.cursor/skills/retrieval/login`, `.cursor/skills/retrieval/videos`, `.cursor/skills/ingest/upload-video`.

Paste this into Lane A's session:

```text
You are Lane A on Off-ice Flag, contract version 1. Read docs/OFF_ICE_FLAG_SPEC.md and docs/AGENT_CONTRACTS.md. Edit only tools/off-ice-flag/vss_client.py. Use the retrieval and upload skills. Do not change schemas. Test against contracts/fixtures until a real clip is indexed. Do not print secrets.
```

### Lane B — Decision

Owns:

- `tools/off-ice-flag/decision.py`

Does:

- Input: one caption string plus the segment timing fields from Lane A.
- Call Weights & Biases chat completions. Base URL defaults to `https://api.inference.wandb.ai/v1`. Override with `WANDB_INFERENCE_BASE`. Auth header `Authorization: Bearer` from `WANDB_API_KEY`.
- On first run, `GET /v1/models` and pin the chosen id in a constant `MODEL_ID` inside `decision.py`. Do not rotate models during the demo.
- Temperature 0. Ask for a JSON object with `contact`, the four signs, and `evidence_quote`. Reject the reply if `evidence_quote` is not contained in the caption, or if the JSON does not match the sign fields.
- Apply the decision rule from the spec in Python. Return one card object, `contract_version` 1, including `stream_path` of `/api/segments/{id}/stream`.
- A failed extraction raises. It does not invent a `come_off`.

Paste this into Lane B's session:

```text
You are Lane B on Off-ice Flag, contract version 1. Read docs/OFF_ICE_FLAG_SPEC.md and docs/AGENT_CONTRACTS.md. Edit only tools/off-ice-flag/decision.py. Signs come from Weights & Biases. The come_off / watch / play_on word comes from the Python rule in the spec. Validate against contracts/card.schema.json. Use contracts/fixtures/tape.json captions as tests. Do not print secrets.
```

### Lane C — Replay UI

Owns:

- `tools/off-ice-flag/static/` (the HTML, CSS, and JS)

Does:

- One page. Fetch `GET /api/tape`. If `status` is not `ready`, show that status and do not invent cards.
- Play `stream_path` in `start_sec` order. Play on and watch continue. Come off pauses.
- Render only the clip, the four yes/no signs, the decision, and the caption.
- Question list from `GET /api/questions`. Clicking an answer seeks to the first `card_ids` entry. A text box `POST`s `/api/ask`.
- Words on screen: Come off, Watch, Play on. Do not write injury, concussion, or severity.

Paste this into Lane C's session:

```text
You are Lane C on Off-ice Flag, contract version 1. Read docs/OFF_ICE_FLAG_SPEC.md and docs/AGENT_CONTRACTS.md. Edit only tools/off-ice-flag/static/. The page plays GET /api/tape and pauses on come_off. Cards show signs, decision, and caption. Questions come from GET /api/questions. Use contracts/fixtures/tape.json as the sample payload. No build step and no medical wording.
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

- `main.py` creates the FastAPI app, serves `static/`, and mounts the routes from the spec. It does not contain the decision rule or the VAST client logic. It calls Lane A and Lane B.
- Precompute the four saved questions into the question schema. `POST /api/ask` calls VAST `POST /api/v1/agent/ask` with this `original_video` only, then fills `card_ids` from overlapping segments.
- `GET /api/eval` compares eight hand labels to the tape using the rule in `docs/EVAL.md`.
- Deploy with `.cursor/skills/deployment/deploy-app-no-registry`. App name `off-ice-flag`. People open the workshop **App** button.
- `requirements.txt` pins `fastapi` and `uvicorn`. Keep the tree small enough for a ConfigMap (about 1 MiB).

Paste this into Lane D's session:

```text
You are Lane D on Off-ice Flag, contract version 1. Read docs/OFF_ICE_FLAG_SPEC.md and docs/AGENT_CONTRACTS.md. You own questions.py, eval.py, main.py, requirements.txt, and deploy/. main.py only wires routes and serves static/. Do not edit vss_client.py, decision.py, or static/. Follow the deploy-app-no-registry skill. Do not print secrets.
```

## Route ownership inside main.py

Lane D registers these and they must keep these shapes:

| Route | Implementation |
|---|---|
| `GET /health` | Lane D |
| `GET /api/tape` | Lane D calls Lane A for segments, Lane B for each card, drops non-contact, sorts by `start_sec` |
| `GET /api/segments/{id}/stream` | Lane A via `vss_client` |
| `GET /api/questions` | Lane D |
| `POST /api/ask` | Lane D |
| `GET /api/eval` | Lane D |

Until `OFF_ICE_ORIGINAL_VIDEO` is set, `GET /api/tape` may return the fixture tape with `video_id` `fixture-period` so Lane C can demo the pause. Set a response header `X-Off-Ice-Fixture: 1` when that happens. Do not present fixture cards as the judged clip.

## Changing a contract

1. Edit the schema and every fixture in one commit.
2. Set `contract_version` to the next integer in the schema `const`, the fixtures, and the top of both docs.
3. Commit message starts with `contract:`.
4. Other lanes stop until they have pulled that commit.

Allowed without a bump: wording inside `docs/OFF_ICE_FLAG_SPEC.md` that does not change a field name, a route, or the decision rule. The decision rule change is a contract bump.
