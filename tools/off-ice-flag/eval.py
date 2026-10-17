"""Lane D: score eight hand labels against the tape (docs/EVAL.md, contract version 2).

Real labels go in labels.json next to this file: a list of eight label objects
(or {"labels": [...]}) in the eval schema shape.
"""

import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
WINDOW_SEC = 5


def load_labels(fixtures_dir, fixture):
    """The eight labels, or None when the real clip has no labels.json yet."""
    path = fixtures_dir / "eval.json" if fixture else HERE / "labels.json"
    if not path.exists():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    return data["labels"] if isinstance(data, dict) else data


def score(labels, cards):
    misses = []
    for label in labels:
        near = [c for c in cards if abs(c["start_sec"] - label["start_sec"]) <= WINDOW_SEC]
        if label["decision"] == "no_card":
            if near:
                misses.append({"label_id": label["id"], "reason": f"expected no card, got {near[0]['id']}"})
            continue
        same = sorted((c for c in near if c["decision"] == label["decision"]),
                      key=lambda c: abs(c["start_sec"] - label["start_sec"]))
        if not same:
            got = near[0]["decision"] if near else "no card"
            misses.append({"label_id": label["id"], "reason": f"expected {label['decision']}, got {got}"})
        elif same[0]["signs"] != label["signs"]:
            diff = sorted(k for k in label["signs"] if label["signs"][k] != same[0]["signs"][k])
            misses.append({"label_id": label["id"], "reason": f"decision matched, signs differ: {', '.join(diff)}"})
    return {"contract_version": 2, "labels": labels, "matched": len(labels) - len(misses),
            "total": 8, "misses": misses}
