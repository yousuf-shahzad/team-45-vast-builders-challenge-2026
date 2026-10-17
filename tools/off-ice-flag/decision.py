"""Lane B: signs from Weights & Biases, decision from Python (contract version 2).

Every W&B HTTP call for Off-ice Flag lives here. Does not import vss_client.py.

    python decision.py models     # list W&B model ids, then pin one in MODEL_ID
"""

import json
import os
import re
import sys
import urllib.error
import urllib.request

# ponytail: pinned without a key on this laptop. Run `python decision.py models`
# on the VM and replace with an id from that list before the demo. Do not rotate.
MODEL_ID = "meta-llama/Llama-3.3-70B-Instruct"

SIGNS = ("head_neck", "slow_to_rise", "left_ice", "favoring_limb")  # spec order for signs_true
DECISION_WORDS = {"come_off": "Come off", "watch": "Watch", "play_on": "Play on"}
# The spec's list (injury, injured, concussion, fracture, fractured, sprain, tear, torn, severe,
# severity, diagnosis, medical) matched by stem so "injuries" or "medically" do not slip through,
# plus words that claim a medical state. Used on every model text the app shows.
BANNED = re.compile(r"\b(injur\w*|concuss\w*|fractur\w*|sprain\w*|tears?|torn|severe(ly)?|severity|"
                    r"diagnos\w*|medic(al|ally|ine|ation)?|hurt(s|ing)?|pain(ful|s)?|trauma\w*|"
                    r"dislocat\w*|contusion\w*|laceration\w*)\b", re.I)
REDACTED = "[...]"
MAX_SENTENCE = 800


class UpstreamError(Exception):
    """W&B failed, or a reply failed the checks. Routes map this to 502."""


# --- W&B ---------------------------------------------------------------------

def _base():
    return os.environ.get("WANDB_INFERENCE_BASE", "https://api.inference.wandb.ai/v1").rstrip("/")


def _headers():
    key = os.environ.get("WANDB_API_KEY")
    if not key:
        raise UpstreamError("WANDB_API_KEY is not set")
    h = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    if os.environ.get("WANDB_PROJECT"):  # "entity/project", for W&B usage tracking
        h["OpenAI-Project"] = os.environ["WANDB_PROJECT"]
    return h


def _chat(messages):
    body = {"model": MODEL_ID, "messages": messages, "temperature": 0, "max_tokens": 1500}
    req = urllib.request.Request(_base() + "/chat/completions", data=json.dumps(body).encode(), headers=_headers())
    try:
        with urllib.request.urlopen(req, timeout=90) as r:
            return json.load(r)["choices"][0]["message"]["content"] or ""
    except urllib.error.HTTPError as e:
        raise UpstreamError(f"wandb chat: {e.code}") from None
    except (urllib.error.URLError, KeyError, IndexError, ValueError) as e:
        raise UpstreamError(f"wandb chat: {type(e).__name__}") from None


def _json_object(text):
    """The first {...} object in a reply, tolerating code fences or a preamble."""
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end < start:
        raise ValueError("no JSON object in reply")
    obj = json.loads(text[start:end + 1])
    if not isinstance(obj, dict):
        raise ValueError("reply is not an object")
    return obj


def models():
    req = urllib.request.Request(_base() + "/models", headers=_headers())
    with urllib.request.urlopen(req, timeout=30) as r:
        return [m["id"] for m in json.load(r).get("data", [])]


# --- signs -------------------------------------------------------------------

SIGNS_SYSTEM = (
    "You read one caption of a hockey video segment and report only what the caption states. "
    "Reply with one JSON object and nothing else."
)
SIGNS_USER = """Caption:
\"\"\"{caption}\"\"\"

Return exactly these keys:
{{"contact": bool, "head_neck": bool, "slow_to_rise": bool, "left_ice": bool, "favoring_limb": bool, "evidence_quote": string}}

- contact: players make body contact (a hit, check, bump, or collision).
- head_neck: the head or neck is involved in the contact.
- slow_to_rise: a player is slow to get up or stays down.
- left_ice: a player skates to the bench or leaves the ice.
- favoring_limb: a player favors an arm or a leg after the contact.
A field is true only when the caption states it. A negated statement ("not involved", "no one stays down") is false.
evidence_quote: words copied exactly from the caption that support the true signs, or the contact when no sign is true. Use "" when there is no contact."""


def _locate(quote, caption):
    """The caption's own text for quote (case and edge punctuation forgiven), or None."""
    q = quote.strip().strip("\"'“”‘’.,;: ")
    if not q:
        return None
    i = caption.lower().find(q.lower())
    return caption[i:i + len(q)] if i >= 0 else None


def _check_signs(reply, caption):
    obj = _json_object(reply)
    for k in ("contact",) + SIGNS:
        if not isinstance(obj.get(k), bool):
            raise ValueError(f"{k} must be true or false")
    out = {k: obj[k] for k in ("contact",) + SIGNS}
    if not out["contact"]:
        out["evidence_quote"] = ""
        return out
    quote = obj.get("evidence_quote")
    found = _locate(quote, caption) if isinstance(quote, str) else None
    if not found:
        raise ValueError("evidence_quote must be copied exactly from the caption")
    if BANNED.search(found):
        raise ValueError("evidence_quote must quote what is visible, without words that name an injury or "
                         "a medical state; pick other words from the caption")
    out["evidence_quote"] = found
    return out


def redact(text):
    """Caption as shown on the card: words that name an injury or a medical state are cut."""
    return BANNED.sub(REDACTED, text)


def extract_signs(caption):
    """{contact, four signs, evidence_quote} from W&B. Raises UpstreamError; never guesses."""
    messages = [{"role": "system", "content": SIGNS_SYSTEM},
                {"role": "user", "content": SIGNS_USER.format(caption=caption)}]
    reply = _chat(messages)
    try:
        return _check_signs(reply, caption)
    except ValueError as e:
        # One correction turn; temperature 0 would repeat the same reply otherwise.
        messages += [{"role": "assistant", "content": reply},
                     {"role": "user", "content": f"That reply was rejected: {e}. Reply with the corrected JSON object only."}]
        try:
            return _check_signs(_chat(messages), caption)
        except ValueError as e2:
            raise UpstreamError(f"sign extraction rejected: {e2}") from None


# --- rule --------------------------------------------------------------------

def signs_true(signs):
    return [k for k in SIGNS if signs[k]]


def decide(contact, signs):
    """The spec rule. None means no contact: the segment is not a card."""
    if not contact:
        return None
    n = len(signs_true(signs))
    if signs["left_ice"] or signs["slow_to_rise"] or n >= 2:
        return "come_off"
    return "watch" if n == 1 else "play_on"


def clause(signs):
    """Which rule bullet fired. First match wins."""
    n = len(signs_true(signs))
    if signs["left_ice"]:
        return "left_ice"
    if signs["slow_to_rise"]:
        return "slow_to_rise"
    if n >= 2:
        return "two_or_more"
    return "one_sign" if n == 1 else "no_signs"


def card_from_signs(segment, extracted):
    """Card schema object, or None for a non-contact segment."""
    signs = {k: extracted[k] for k in SIGNS}
    decision = decide(extracted["contact"], signs)
    if decision is None:
        return None
    return {
        "contract_version": 2,
        "id": segment["id"],
        "source": segment.get("source", ""),
        "start_sec": segment["start_sec"],
        "end_sec": segment["end_sec"],
        "caption": redact(segment["caption"]),  # the quote has no banned word, so it survives
        "contact": True,
        "signs": signs,
        "evidence_quote": extracted["evidence_quote"],
        "decision": decision,
        "stream_path": f"/api/segments/{segment['id']}/stream",
    }


def make_card(segment):
    """One segment from Lane A -> one card (W&B signs + Python rule), or None."""
    return card_from_signs(segment, extract_signs(segment["caption"]))


# --- why sentence ------------------------------------------------------------

def trace(card):
    """What W&B sees for a why sentence: id, decision, clause, signs, evidence_quote. Nothing else."""
    s = card["signs"]
    return {"id": card["id"], "decision": card["decision"], "clause": clause(s), "signs": s,
            "evidence_quote": card["evidence_quote"]}


WHY_SYSTEM = (
    "You write one short explanation per card for a hockey trainer. Use only the trace you are "
    "given. Reply with one JSON object and nothing else."
)
WHY_USER = """Traces:
{traces}

Return {{"sentences": [{{"id": "<card id>", "sentence": "<text>"}}]}} with one entry per trace.

Each sentence must:
- start with the decision words exactly: "Come off." for come_off, "Watch." for watch, "Play on." for play_on;
- name the true signs in plain words: head, slow to rise, leaves the ice, favors a limb (or say no signs are true);
- include the evidence_quote word for word, after "The caption says";
- stay under 300 characters and only describe what is visible.
- never name another decision, never guess what is wrong with a player, and never use these words: injury, injured, concussion, fracture, sprain, tear, torn, severe, severity, diagnosis, medical, hurt, pain.

Example for a come_off trace with slow_to_rise and left_ice true:
"Come off. Slow to rise and leaves the ice are true. The caption says stays down, then gets up slowly and skates to the bench." """


OTHER_DECISIONS = {
    d: re.compile(r"(?<![a-z])(" + "|".join(w.lower() for k, w in DECISION_WORDS.items() if k != d) + r")(?![a-z])")
    for d in DECISION_WORDS
}


def sentence_ok(sentence, card):
    """The spec checks for one why sentence, plus: it does not name another decision."""
    if not isinstance(sentence, str) or not sentence.strip() or len(sentence) > MAX_SENTENCE:
        return False
    low, quote = sentence.lower(), card["evidence_quote"].lower()
    return (quote in low
            and DECISION_WORDS[card["decision"]].lower() in low
            and not OTHER_DECISIONS[card["decision"]].search(low.replace(quote, ""))
            and not BANNED.search(sentence))


def why_object(card, sentence):
    s = card["signs"]
    return {"contract_version": 2, "card_id": card["id"], "decision": card["decision"], "clause": clause(s),
            "signs_true": signs_true(s), "evidence_quote": card["evidence_quote"], "sentence": sentence.strip()}


WHY_BATCH = 20  # cards per call; a full game has too many cards for one reply


def phrase_whys(cards):
    """{card_id: why object}, one W&B call per WHY_BATCH cards. Rejected or failed -> absent."""
    out = {}
    for i in range(0, len(cards), WHY_BATCH):
        batch = cards[i:i + WHY_BATCH]
        traces = json.dumps([trace(c) for c in batch], indent=1)
        try:
            reply = _chat([{"role": "system", "content": WHY_SYSTEM},
                           {"role": "user", "content": WHY_USER.format(traces=traces)}])
            items = _json_object(reply).get("sentences") or []
        except (UpstreamError, ValueError):
            continue
        by_id = {c["id"]: c for c in batch}
        for item in items:
            card = by_id.get(item.get("id")) if isinstance(item, dict) else None
            if card and sentence_ok(item.get("sentence"), card):
                out[card["id"]] = why_object(card, item["sentence"])
    return out


if __name__ == "__main__":
    if sys.argv[1:] == ["models"]:
        print("\n".join(models()))
    else:
        sys.exit(__doc__)
