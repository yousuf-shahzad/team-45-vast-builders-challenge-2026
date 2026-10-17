"""Lane A: every VAST HTTP call for Off-ice Flag (contract version 2).

Login, upload, segments, stream proxy, stored detections, synthesize, agent ask.
Reads VSS_URL / VSS_USERNAME / VSS_PASSWORD from the environment. The JWT never
leaves this module. Does not import decision.py.

CLI (run on the VM, where the team config is):
    python vss_client.py login
    python vss_client.py split <game.mp4>           # lossless parts under the upload cap
    python vss_client.py upload <file> [<file> ...] # asks once, then uploads each
    python vss_client.py find <filename-part>       # find original_video after upload
    python vss_client.py reingest <uri> [<uri> ...] # re-caption with the current ingest prompt. Asks first
    python vss_client.py segments <original_video>  # raw + mapped rows, to check field names
"""

import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEFAULT_UPLOAD_MB = 100  # only if /api/v1/config does not say
VIDEO_EXTS = {".mp4", ".mov", ".webm", ".avi", ".mkv"}
SEGMENT_SEC = 5.0  # pipeline cuts ~5 s segments; only used when a row has no timing fields

RECAP_SYSTEM_PROMPT = (
    "Summarize only what the segment captions show, in order. Do not say come off, watch, "
    "or play on. Do not name an injury or its severity. Do not add people or actions that "
    "are not in the captions."
)


class UpstreamError(Exception):
    """VAST failed or answered something unusable. Routes map this to 502."""


class NotFound(UpstreamError):
    pass


_lock = threading.Lock()
_token = None


def _base():
    url = os.environ.get("VSS_URL", "").rstrip("/")
    if not url:
        raise UpstreamError("VSS_URL is not set")
    return url


def _login():
    global _token
    body = {"username": os.environ.get("VSS_USERNAME", ""), "password": os.environ.get("VSS_PASSWORD", "")}
    req = urllib.request.Request(
        _base() + "/api/v1/auth/login",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            _token = json.load(r)["access_token"]
    except (urllib.error.URLError, KeyError, ValueError) as e:
        raise UpstreamError(f"login failed: {type(e).__name__}") from None
    return _token


def _get_token():
    with _lock:
        return _token or _login()


def _open(path, *, method="GET", params=None, body=None, headers=None, token_in_query=False, timeout=60):
    """Open a backend URL with the JWT. Re-logs in once on 401. Returns the raw response."""
    global _token
    for attempt in (0, 1):
        token = _get_token()
        q = dict(params or {})
        h = dict(headers or {})
        if token_in_query:
            q["token"] = token
        else:
            h["Authorization"] = f"Bearer {token}"
        url = _base() + path + ("?" + urllib.parse.urlencode(q) if q else "")
        req = urllib.request.Request(url, data=body, headers=h, method=method)
        try:
            return urllib.request.urlopen(req, timeout=timeout)
        except urllib.error.HTTPError as e:
            if e.code == 401 and attempt == 0:
                with _lock:
                    _token = None
                continue
            if e.code == 404:
                raise NotFound(f"{method} {path}: 404") from None
            raise UpstreamError(f"{method} {path}: {e.code}") from None
        except urllib.error.URLError as e:
            raise UpstreamError(f"{method} {path}: {e.reason}") from None
    raise UpstreamError(f"{method} {path}: 401 after re-login")


def _json(path, *, method="GET", params=None, payload=None, timeout=60):
    body = json.dumps(payload).encode() if payload is not None else None
    headers = {"Content-Type": "application/json"} if payload is not None else None
    with _open(path, method=method, params=params, body=body, headers=headers, timeout=timeout) as r:
        try:
            return json.load(r)
        except ValueError:
            raise UpstreamError(f"{method} {path}: not JSON") from None


# --- tolerant field lookup -------------------------------------------------
# ponytail: segment/evidence field names are read off the skills, not a real
# response. Run `python vss_client.py segments <uri>` on the VM once and trim
# these key lists to what the backend actually sends.

def _first(d, keys, default=None):
    for k in keys:
        if isinstance(d, dict) and d.get(k) not in (None, ""):
            return d[k]
    return default


def _rows(obj, keys=("segments", "results", "items", "data", "videos", "chunks", "evidence")):
    """The list of row dicts in a response that is a list or wraps one."""
    if isinstance(obj, list):
        return [x for x in obj if isinstance(x, dict)]
    if isinstance(obj, dict):
        for k in keys:
            if isinstance(obj.get(k), list):
                return [x for x in obj[k] if isinstance(x, dict)]
    return []


START_KEYS = ("start_sec", "segment_start_sec", "start_time", "segment_start", "start", "best_match_start_sec")
END_KEYS = ("end_sec", "segment_end_sec", "end_time", "segment_end", "end", "best_match_end_sec")
CAPTION_KEYS = ("reasoning_content", "caption", "description", "reasoning", "text")


def span(row):
    """(source, start_sec, end_sec) for an evidence/segment row. Times may be None."""
    source = _first(row, ("source", "preview_source", "segment_source"))
    start, end = _first(row, START_KEYS), _first(row, END_KEYS)
    num = _first(row, ("segment_number", "segment_index"))
    if start is None and num is not None:
        start = (float(num) - 1) * SEGMENT_SEC
    if start is not None and end is None:
        end = float(start) + SEGMENT_SEC
    return source, (float(start) if start is not None else None), (float(end) if end is not None else None)


def map_segment(row, index):
    """A VSS segment row -> the fields Lane B needs."""
    source, start, end = span(row)
    caption = _first(row, CAPTION_KEYS, "")
    if not isinstance(caption, str):
        caption = json.dumps(caption)
    num = _first(row, ("segment_number", "segment_index"))
    seg_id = f"seg-{int(num):03d}" if num is not None else f"seg-{index + 1:03d}"
    return {
        "id": seg_id,
        "source": source or "",
        "start_sec": start if start is not None else index * SEGMENT_SEC,
        "end_sec": end if end is not None else (index + 1) * SEGMENT_SEC,
        "caption": caption.strip(),
    }


# --- API -------------------------------------------------------------------

def segments(original_video):
    """Indexed segments of one parent video, sorted by start time, plus the tape status.

    Returns (status, rows). status is ready / indexing / missing_clip.
    """
    try:
        # ponytail: `limit` is a guess at the param name; a full game is ~700+ segments. If the
        # CLI shows fewer rows than expected_segments, find the real paging param.
        raw = _json("/api/v1/tools/segments", params={"original_video": original_video, "limit": 5000})
    except NotFound:
        raw = []
    rows = [map_segment(r, i) for i, r in enumerate(_rows(raw))]
    rows = list({r["id"]: r for r in rows if r["source"]}.values())  # re-ingest can leave duplicate slots
    rows.sort(key=lambda r: r["start_sec"])

    status = "ready" if rows else "missing_clip"
    stats = _json("/api/v1/dashboard/stats", params={"scope": "all"})
    for v in stats.get("recent_videos") or []:
        if v.get("original_video") == original_video:
            expected = v.get("expected_segments") or 0
            if not rows or (v.get("unique_segments") or 0) < expected:
                status = "indexing"
    return status, rows


def stream(source, range_header=None):
    """Open the VSS stream for one segment. Returns (status, headers, chunk iterator)."""
    headers = {"Range": range_header} if range_header else None
    r = _open("/api/v1/videos/stream", params={"source": source}, headers=headers, token_in_query=True, timeout=120)
    keep = ("Content-Type", "Content-Length", "Content-Range", "Accept-Ranges")
    out = {k: r.headers[k] for k in keep if r.headers.get(k)}

    def chunks():
        with r:
            while block := r.read(64 * 1024):
                yield block

    return r.status, out, chunks()


def _label_counts(sidecar):
    """Label -> count. Counts detection objects (dicts with a string `label` or `class_name`).

    Each list of detection objects is one frame; a label's count is its max over
    frames, so 30 frames of 2 people read "person 2", not "person 60".
    """
    best = defaultdict(int)

    def walk(node):
        if isinstance(node, dict):
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            frame = defaultdict(int)
            for item in node:
                label = _first(item, ("label", "class_name")) if isinstance(item, dict) else None
                if isinstance(label, str) and label.strip():
                    frame[label.strip()] += 1
                else:
                    walk(item)
            for label, n in frame.items():
                best[label] = max(best[label], n)

    walk(sidecar)
    return dict(best)


def detections(seg_id, source):
    """Detections schema object for one segment. A 404 or no labels -> available false."""
    try:
        sidecar = _json("/api/v1/videos/detections", params={"source": source})
    except NotFound:
        sidecar = None
    counts = _label_counts(sidecar)
    objects = [{"label": k, "count": v} for k, v in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))]
    return {"contract_version": 2, "id": seg_id, "available": bool(objects), "objects": objects}


def synthesize(original_video, question="What happens in this clip?"):
    """Clip recap over all segments. Returns (answer, spans) where spans are (source, start, end)."""
    res = _json(
        "/api/v1/videos/synthesize",
        method="POST",
        payload={"original_video": original_video, "question": question,
                 "system_prompt": RECAP_SYSTEM_PROMPT, "max_segments": 60},
        timeout=180,
    )
    answer = _first(res, ("answer",)) or _first(res.get("llm_synthesis") or {}, ("response",))
    if not isinstance(answer, str) or not answer.strip():
        raise UpstreamError("synthesize: empty answer")
    used = res.get("segments_used")
    spans = [span(r) for r in _rows(used)] if isinstance(used, (list, dict)) else []
    return answer.strip(), spans


def agent_ask(question, original_video):
    """VAST agent ask scoped to this clip. Returns (answer, spans)."""
    res = _json(
        "/api/v1/agent/ask",
        method="POST",
        payload={"question": question, "original_video": original_video, "top_k": 10},
        timeout=120,
    )
    answer = res.get("answer")
    if not isinstance(answer, str) or not answer.strip():
        raise UpstreamError("agent ask: empty answer")
    return answer.strip(), [span(r) for r in _rows(res.get("evidence"))]


SEARCH_TAG = "off-ice-flag"  # upload() tags the game with it, so search stays on our footage
UPLOAD_FIELDS = {"is_public": "false", "scenario": "nhl", "camera_id": "rink_cam-1", "location": "rink",
                 "tags": SEARCH_TAG}


def search(query, top_k=50):
    """VAST hybrid search (caption text + visual vectors) over our uploads -> segment sources, best first."""
    res = _json(
        "/api/v1/search",
        method="POST",
        payload={"query": query, "top_k": top_k, "llm_top_n": 0, "min_similarity": 0.25,
                 "tags": [SEARCH_TAG], "include_public": True},
    )
    return [s for s in (_first(r, ("source",)) for r in _rows(res, ("results",))) if s]


def ingest_prompt():
    """The custom prompt sent on upload. The contracts copy wins when both exist."""
    for p in (HERE.parent.parent / "contracts" / "ingest_prompt.txt", HERE / "ingest_prompt.txt"):
        if p.exists():
            return p.read_text(encoding="utf-8").strip()
    raise FileNotFoundError("ingest_prompt.txt")


def upload_cap_mb():
    """The backend's live upload cap (upload skill: read it, don't hardcode)."""
    cfg = _json("/api/v1/config")
    return float(_first(cfg.get("app") or {}, ("max_upload_size_mb",)) or _first(cfg, ("max_upload_size_mb",))
                 or DEFAULT_UPLOAD_MB)


def _ffmpeg():
    """ffmpeg on PATH, or the static build from `pip install imageio-ffmpeg` (no admin needed)."""
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except ImportError:
        return shutil.which("ffmpeg")


def _duration(exe, path):
    """Seconds, read from ffmpeg's own banner (imageio-ffmpeg ships no ffprobe)."""
    probe = subprocess.run([exe, "-i", str(path)], capture_output=True, text=True).stderr
    m = re.search(r"Duration: (\d+):(\d+):([\d.]+)", probe)
    return int(m[1]) * 3600 + int(m[2]) * 60 + float(m[3]) if m else None


def fit_hint(path, cap_mb):
    """An ffmpeg command that re-encodes a long game under the cap, so it stays one upload."""
    exe = _ffmpeg()
    if not exe:
        return "no ffmpeg found: run `pip install imageio-ffmpeg`, then run this command again"
    duration = _duration(exe, path)
    if not duration:
        return f"could not read the duration of {path.name}"
    kbps = int(cap_mb * 8000 * 0.92 / duration)  # video only; audio is dropped (-an)
    # ponytail: rough bitrate ladder for a wide rink camera; Cosmos samples frames, so fps can drop first.
    height, fps = (720, 30) if kbps >= 1500 else (540, 15) if kbps >= 500 else (360, 15)
    return (f'"{exe}" -i "{path}" -vf scale=-2:{height},fps={fps} -c:v libx264 -preset fast -b:v {kbps}k '
            f'-maxrate {kbps}k -bufsize {2 * kbps}k -an "{path.with_name(path.stem + "_fit.mp4")}"'
            f"\n  # {duration / 60:.0f} min at {kbps} kbps, {height}p {fps} fps")


def split(path, cap_mb):
    """Cut a long game into parts under the cap by stream copy: no re-encode, no quality loss.

    Returns the part files in game order. Cuts land on keyframes, so a part can run long;
    the target shrinks until every part fits.
    """
    path = Path(path)
    exe = _ffmpeg()
    if not exe:
        raise RuntimeError("no ffmpeg found: run `pip install imageio-ffmpeg`")
    duration = _duration(exe, path)
    if not duration:
        raise RuntimeError(f"could not read the duration of {path.name}")
    cap_bytes = cap_mb * 1024 * 1024
    stem = f"{path.stem}_part_"
    for fill in (0.85, 0.6, 0.4):
        for old in path.parent.glob(f"{stem}*.mp4"):
            old.unlink()
        part_sec = max(30, int(duration * cap_bytes * fill / path.stat().st_size))
        subprocess.run([exe, "-hide_banner", "-loglevel", "error", "-i", str(path), "-map", "0:v:0", "-an",
                        "-c", "copy", "-f", "segment", "-segment_time", str(part_sec), "-reset_timestamps", "1",
                        str(path.with_name(stem + "%03d.mp4"))], check=True)
        parts = sorted(path.parent.glob(f"{stem}*.mp4"))
        if all(q.stat().st_size <= cap_bytes for q in parts):
            return parts
    raise RuntimeError("a part is still over the cap; the video has very long keyframe gaps")


def upload(path, cap_mb=DEFAULT_UPLOAD_MB):
    """Multipart upload with scenario=nhl and the contract prompt. Returns the API response.

    The DataEngine segmenter cuts each file into ~5 s segments itself. A game over the
    cap goes up as split() parts, one call each.
    """
    path = Path(path)
    if path.suffix.lower() not in VIDEO_EXTS:
        raise ValueError(f"extension {path.suffix} not allowed")
    if path.stat().st_size > cap_mb * 1024 * 1024:
        raise ValueError(f"file is over the {cap_mb:.0f} MB upload cap. Re-encode it: {fit_hint(path, cap_mb)}")
    fields = dict(UPLOAD_FIELDS, custom_prompt=ingest_prompt())
    boundary = uuid.uuid4().hex
    parts = []
    for k, v in fields.items():
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode())
    parts.append(
        f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{path.name}"\r\n'
        f"Content-Type: application/octet-stream\r\n\r\n".encode()
        + path.read_bytes() + b"\r\n"
    )
    parts.append(f"--{boundary}--\r\n".encode())
    with _open("/api/v1/videos/upload", method="POST", body=b"".join(parts),
               headers={"Content-Type": f"multipart/form-data; boundary={boundary}"}, timeout=600) as r:
        return json.load(r)


def reingest(original_video):
    """Re-run detector -> reasoner -> embedder -> writer on one indexed upload with the contract prompt.

    VAST replaces each segment slot in place (reingest-videos skill). Returns the job id.
    """
    res = _json("/api/v1/dashboard/reingest", method="POST", payload={
        "original_video": original_video, "chunk_count": 1, "scenario": UPLOAD_FIELDS["scenario"],
        "custom_prompt": ingest_prompt()})
    job = res.get("job_id")
    if not job:
        raise UpstreamError("reingest: no job_id")
    return job


def reingest_status(job_id):
    """Job progress, or None when the backend no longer knows the job (it restarted)."""
    try:
        return _json(f"/api/v1/dashboard/reingest/{urllib.parse.quote(str(job_id))}")
    except NotFound:
        return None


def library():
    """Every fully indexed upload in the team archive, as the app's loader shows it."""
    out, offset = [], 0
    while True:
        page = _json("/api/v1/videos/explore", params={"scope": "all", "limit": 100, "offset": offset})
        rows = _rows(page)
        for r in rows:
            meta = r.get("metadata") if isinstance(r.get("metadata"), dict) else {}
            camera = _first(r, ("camera_id",)) or _first(meta, ("camera_id",)) or ""
            location = _first(r, ("location",)) or _first(meta, ("location",)) or ""
            out.append({"original_video": r.get("original_video"), "filename": r.get("filename") or "",
                        "clips": len(r.get("timeline") or []),
                        "ours": camera == UPLOAD_FIELDS["camera_id"] or location == UPLOAD_FIELDS["location"]})
        offset += 100
        total = page.get("total", 0) if isinstance(page, dict) else 0
        if not rows or offset >= total:
            return [v for v in out if v["original_video"]]


def find(name_part):
    return [v for v in library() if name_part.lower() in v["filename"].lower()]


def _cli(argv):
    cmd, args = (argv[0] if argv else None), argv[1:]
    if cmd == "login":
        _login()
        print("login ok")
    elif cmd == "split" and args:
        parts = split(Path(args[0]), upload_cap_mb())
        for q in parts:
            print(f"{q}\t{q.stat().st_size / 1e6:.0f} MB")
        print(f"Next: python vss_client.py upload {Path(args[0]).stem}_part_*.mp4")
    elif cmd == "upload" and args:
        files = [Path(a) for a in args]
        cap = upload_cap_mb()
        big = [q for q in files if q.stat().st_size > cap * 1024 * 1024]
        if big:
            sys.exit(f"{big[0].name} is over the {cap:.0f} MB cap. Split it losslessly into parts:\n"
                     f"  python vss_client.py split {big[0]}\nor re-encode it into one smaller file:\n"
                     f"  {fit_hint(big[0], cap)}")
        total = sum(q.stat().st_size for q in files) / 1e6
        print(f"Upload {len(files)} file(s), {total:.0f} MB in all, scenario=nhl, custom_prompt "
              f"({len(ingest_prompt())} chars), private:")
        for q in files:
            print(f"  {q.name}\t{q.stat().st_size / 1e6:.0f} MB")
        print("Type yes to send:", end=" ")
        if input().strip().lower() != "yes":
            sys.exit("not sent")
        for q in files:
            print(q.name, "->", upload(q, cap).get("object_key"), flush=True)
        print("Next: once indexed, open the app's Split game or Whole video tab and load them.")
    elif cmd == "reingest" and args:
        print(f"Re-ingest {len(args)} video(s) with the current ingest prompt ({len(ingest_prompt())} chars). "
              "Every segment is captioned again on the shared GPUs; this takes minutes. One person per team "
              "should run it.")
        for uri in args:
            print(" ", uri)
        print("Type yes to start:", end=" ")
        if input().strip().lower() != "yes":
            sys.exit("not started")
        jobs = {uri: reingest(uri) for uri in args}
        while jobs:
            time.sleep(4)
            for uri, job in list(jobs.items()):
                s = reingest_status(job)
                name = uri.rsplit("/", 1)[-1]
                if s is None:
                    print(f"{name}: backend lost the job record (it may still be running); check the dashboard")
                    del jobs[uri]
                    continue
                print(f"{name}: {s.get('status')} {s.get('indexed_segments', '?')}/{s.get('total_segments', '?')} clips",
                      flush=True)
                if s.get("status") in ("completed", "failed", "error"):
                    del jobs[uri]
        print("Done. In the app, open the tab and Load videos again to rebuild the tape from the new captions.")
    elif cmd == "find" and args:
        for v in find(args[0]):
            print(v["original_video"], v["filename"], v["clips"], sep="\t")
    elif cmd == "segments" and args:
        arg = args[0]
        raw = _json("/api/v1/tools/segments", params={"original_video": arg, "limit": 5000})
        first = _rows(raw)[:1]
        print("raw first row keys:", sorted(first[0]) if first else "none")
        status, rows = segments(arg)
        stats = _json("/api/v1/dashboard/stats", params={"scope": "all"})
        exp = [v.get("expected_segments") for v in stats.get("recent_videos") or [] if v.get("original_video") == arg]
        print("status:", status, "segments:", len(rows), "expected:", exp[0] if exp else "not in recent_videos")
        for r in rows[:5]:
            print(json.dumps(r)[:300])
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    _cli(sys.argv[1:])
