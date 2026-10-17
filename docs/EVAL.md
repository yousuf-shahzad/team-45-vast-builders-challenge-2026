# Off-ice Flag evaluation

Contract version 1. Lane D owns this file. The check set is eight moments, labeled by a person while watching the clip, before the match count is treated as real.

## Inputs

- The trimmed file name, size in MB, and duration.
- The ingest prompt in [contracts/ingest_prompt.txt](../contracts/ingest_prompt.txt), sent as `custom_prompt` on upload. Scenario label: `nhl`.
- `original_video` URI once the upload returns.
- Hand labels: eight rows. Each row is a time, a decision (`come_off`, `watch`, `play_on`, or `no_card`), the four signs, and a one-line note of what was visible.

## Outputs

- `GET /api/tape` cards. Contact only, sorted by `start_sec`.
- Each card's `decision` is produced by the Python rule in the spec, not by a free-form model sentence.
- `GET /api/eval` with `matched`, `total` (always 8), and `misses`.

## How a row matches

A label matches when a card starts within 5 seconds of `start_sec` and has the same `decision`. A `no_card` label matches when no card starts within 5 seconds. Sign mismatches are listed in `misses` even if the decision matched, and they still count as a miss.

## Placeholder

[contracts/fixtures/eval.json](../contracts/fixtures/eval.json) is the shape and a perfect score against the fixture tape. Replace `matched` and `misses` after the real clip is labeled. Do not leave the fixture score in the demo note.

## Demo note

One short paragraph: file, prompt, eight labels, how many matched, and any miss a judge might see. That paragraph is what the technical criterion asks for.
