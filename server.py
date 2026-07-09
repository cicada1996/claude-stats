#!/usr/bin/env python3
"""Claude Stats — local dashboard for Claude Code token usage.

Reads ~/.claude/projects/**/*.jsonl (Claude Code's own transcript files),
aggregates token usage, and serves a dashboard at http://localhost:8787.

No dependencies — Python 3.9+ stdlib only.  Run:  python3 server.py
"""

import glob
import json
import os
import threading
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

PORT = 8787
PROJECTS_DIR = os.path.expanduser("~/.claude/projects")
BLOCK_HOURS = 5

# API-equivalent pricing, USD per million tokens:
# (input, output, cache_read, cache_write_5m, cache_write_1h)
PRICING = {
    "claude-fable-5":    (10.0, 50.0, 1.00, 12.50, 20.0),
    "claude-opus-4-8":   (5.0,  25.0, 0.50, 6.25,  10.0),
    "claude-opus-4-7":   (5.0,  25.0, 0.50, 6.25,  10.0),
    "claude-opus-4-6":   (5.0,  25.0, 0.50, 6.25,  10.0),
    "claude-sonnet-4-6": (3.0,  15.0, 0.30, 3.75,  6.0),
    "claude-sonnet-4-5": (3.0,  15.0, 0.30, 3.75,  6.0),
    "claude-sonnet-5":   (3.0,  15.0, 0.30, 3.75,  6.0),
    "claude-haiku-4-5":  (1.0,  5.0,  0.10, 1.25,  2.0),
}
DEFAULT_PRICING = (5.0, 25.0, 0.50, 6.25, 10.0)  # unknown models -> Opus-tier

MODEL_LABELS = {
    "claude-fable-5": "Fable 5",
    "claude-opus-4-8": "Opus 4.8",
    "claude-opus-4-7": "Opus 4.7",
    "claude-opus-4-6": "Opus 4.6",
    "claude-sonnet-4-6": "Sonnet 4.6",
    "claude-sonnet-4-5": "Sonnet 4.5",
    "claude-sonnet-5": "Sonnet 5",
    "claude-haiku-4-5": "Haiku 4.5",
}


def project_label(dirname):
    """Turn '-Users-danielkang-Documents-Code-md-opener' into 'md-opener'."""
    name = dirname
    for prefix in ("-Users-danielkang-Documents-Code-",
                   "-Users-danielkang-Documents-Projects-",
                   "-Users-danielkang-Documents-",
                   "-Users-danielkang-Desktop-",
                   "-Users-danielkang--",
                   "-Users-danielkang-"):
        if name.startswith(prefix):
            rest = name[len(prefix):]
            return rest if rest else "Home"
    if name == "-Users-danielkang":
        return "Home"
    return name.lstrip("-")


# ---------------------------------------------------------------- parsing ---

_file_cache = {}  # path -> (mtime, size, [entry, ...])
_cache_lock = threading.Lock()


def parse_file(path):
    """Extract usage entries from one transcript file."""
    project = project_label(os.path.basename(os.path.dirname(path)))
    entries = []
    with open(path, "r", errors="replace") as fh:
        for line in fh:
            try:
                d = json.loads(line)
            except (json.JSONDecodeError, ValueError):
                continue
            if d.get("type") != "assistant":
                continue
            msg = d.get("message") or {}
            usage = msg.get("usage") or {}
            ts = d.get("timestamp")
            if not ts:
                continue
            inp = usage.get("input_tokens") or 0
            out = usage.get("output_tokens") or 0
            cr = usage.get("cache_read_input_tokens") or 0
            cw_total = usage.get("cache_creation_input_tokens") or 0
            cc = usage.get("cache_creation") or {}
            cw1h = cc.get("ephemeral_1h_input_tokens") or 0
            cw5m = cc.get("ephemeral_5m_input_tokens") or 0
            if cw1h + cw5m == 0:
                cw5m = cw_total
            tools = [c.get("name") for c in (msg.get("content") or [])
                     if isinstance(c, dict) and c.get("type") == "tool_use"]
            if inp + out + cr + cw_total == 0 and not tools:
                continue
            try:
                t = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            except ValueError:
                continue
            entries.append({
                "t": t.timestamp(),
                "model": msg.get("model") or "unknown",
                "project": project,
                "session": d.get("sessionId"),
                "side": bool(d.get("isSidechain")),
                "dedup": f"{msg.get('id')}:{d.get('requestId')}",
                "in": inp, "out": out, "cr": cr, "cw5": cw5m, "cw1": cw1h,
                "tools": tools,
            })
    return entries


def load_entries():
    """All usage entries across all transcript files, deduplicated, sorted."""
    paths = glob.glob(os.path.join(PROJECTS_DIR, "*", "*.jsonl"))
    with _cache_lock:
        for path in paths:
            try:
                st = os.stat(path)
            except OSError:
                continue
            key = (st.st_mtime, st.st_size)
            cached = _file_cache.get(path)
            if cached is None or (cached[0], cached[1]) != key:
                _file_cache[path] = (st.st_mtime, st.st_size, parse_file(path))
        # drop deleted files
        for path in list(_file_cache):
            if path not in paths:
                del _file_cache[path]
        all_entries = [e for _, _, ents in _file_cache.values() for e in ents]
    seen, out = set(), []
    for e in sorted(all_entries, key=lambda e: e["t"]):
        if e["dedup"] in seen:
            continue
        seen.add(e["dedup"])
        out.append(e)
    return out


def signature():
    """Cheap fingerprint of all transcript files (paths + mtime + size), with
    no parsing. If this is unchanged since the last call, the underlying data
    hasn't changed and there's no need to rebuild the payload."""
    sig = []
    for path in glob.glob(os.path.join(PROJECTS_DIR, "*", "*.jsonl")):
        try:
            st = os.stat(path)
        except OSError:
            continue
        sig.append((path, st.st_mtime, st.st_size))
    sig.sort()
    return tuple(sig)


# ------------------------------------------------------------ aggregation ---

def cost_of(model, inp, out, cr, cw5, cw1):
    p = PRICING.get(model, DEFAULT_PRICING)
    return (inp * p[0] + out * p[1] + cr * p[2] + cw5 * p[3] + cw1 * p[4]) / 1e6


def zero():
    return {"in": 0, "out": 0, "cr": 0, "cw": 0, "cost": 0.0, "msgs": 0}


def add(agg, e):
    agg["in"] += e["in"]
    agg["out"] += e["out"]
    agg["cr"] += e["cr"]
    agg["cw"] += e["cw5"] + e["cw1"]
    agg["cost"] += cost_of(e["model"], e["in"], e["out"], e["cr"], e["cw5"], e["cw1"])
    agg["msgs"] += 1


def total_tokens(agg):
    return agg["in"] + agg["out"] + agg["cr"] + agg["cw"]


def compute_blocks(entries):
    """5-hour billing blocks: a block starts at the top of the hour of the
    first message after the previous block ended, and spans 5 hours."""
    blocks = []
    cur = None
    for e in entries:
        t = e["t"]
        if cur is None or t >= cur["end_ts"]:
            start = datetime.fromtimestamp(t).astimezone().replace(
                minute=0, second=0, microsecond=0)
            cur = {"start_ts": start.timestamp(),
                   "end_ts": (start + timedelta(hours=BLOCK_HOURS)).timestamp(),
                   "agg": zero(), "models": defaultdict(int),
                   "first": t, "last": t}
            blocks.append(cur)
        add(cur["agg"], e)
        cur["models"][e["model"]] += (e["in"] + e["out"] + e["cr"] + e["cw5"] + e["cw1"])
        cur["last"] = t
    return blocks


HISTORY_PATH = os.path.expanduser("~/.claude-stats/history.json")
_last_snapshot = 0.0


def snapshot_history(entries):
    """Persist per-day aggregates to ~/.claude-stats/history.json so the record
    survives even if Claude Code's transcripts are later cleaned up. Past days
    are frozen once captured; today's row keeps updating. Best-effort, 60s throttle."""
    global _last_snapshot
    now = time.time()
    if now - _last_snapshot < 60:
        return
    _last_snapshot = now
    today_key = datetime.now().astimezone().strftime("%Y-%m-%d")
    day_agg = {}
    for e in entries:
        d = datetime.fromtimestamp(e["t"]).astimezone().strftime("%Y-%m-%d")
        a = day_agg.get(d)
        if a is None:
            a = day_agg[d] = {"total": 0, "content": 0, "in": 0, "out": 0,
                              "cr": 0, "cw": 0, "cost": 0.0, "msgs": 0, "models": {}}
        cw = e["cw5"] + e["cw1"]
        a["in"] += e["in"]; a["out"] += e["out"]; a["cr"] += e["cr"]; a["cw"] += cw
        a["total"] += e["in"] + e["out"] + e["cr"] + cw
        a["content"] += e["in"] + e["out"] + cw
        a["cost"] += cost_of(e["model"], e["in"], e["out"], e["cr"], e["cw5"], e["cw1"])
        a["msgs"] += 1
        lbl = MODEL_LABELS.get(e["model"], e["model"])
        a["models"][lbl] = a["models"].get(lbl, 0) + e["in"] + e["out"] + e["cr"] + cw
    for a in day_agg.values():
        a["cost"] = round(a["cost"], 4)
    try:
        store = {}
        if os.path.exists(HISTORY_PATH):
            with open(HISTORY_PATH) as fh:
                store = json.load(fh)
        for d, a in day_agg.items():
            if d == today_key or d not in store:  # freeze past days once captured
                store[d] = a
        os.makedirs(os.path.dirname(HISTORY_PATH), exist_ok=True)
        tmp = HISTORY_PATH + ".tmp"
        with open(tmp, "w") as fh:
            json.dump(store, fh, separators=(",", ":"), sort_keys=True)
        os.replace(tmp, HISTORY_PATH)
    except OSError:
        pass  # history is best-effort; never break the dashboard


def build_payload(days):
    entries = load_entries()
    now = datetime.now().astimezone()
    now_ts = now.timestamp()
    today_key = now.strftime("%Y-%m-%d")

    # Four token measures per aggregate:
    #   all       = input + output + cache reads + cache writes (raw, inflated by re-reads)
    #   processed = input + output + cache writes (unique tokens the model handled)
    #   content   = input + output (your prompts + Claude's replies)
    #   output    = output (what Claude wrote)
    MEAS = ("all", "processed", "content", "output")

    def meas(a):
        io = a["in"] + a["out"]
        return {"all": io + a["cr"] + a["cw"], "processed": io + a["cw"],
                "content": io, "output": a["out"]}

    # --- 5h blocks (always computed over full history) ---
    blocks = compute_blocks(entries)
    block_list = []
    for b in blocks:
        mm = meas(b["agg"])
        block_list.append({
            "start": b["start_ts"], "end": b["end_ts"],
            "tokens": mm["all"],  # back-compat (terminal claudetop.py)
            "all": mm["all"], "processed": mm["processed"],
            "content": mm["content"], "output": mm["output"],
            "cost": round(b["agg"]["cost"], 4),
            "out": b["agg"]["out"], "msgs": b["agg"]["msgs"],
            "models": {MODEL_LABELS.get(m, m): v for m, v in b["models"].items()},
            "active": b["start_ts"] <= now_ts < b["end_ts"],
        })
    active = block_list[-1] if block_list and block_list[-1]["active"] else None
    completed = {k: max((b[k] for b in block_list if not b["active"]), default=0) for k in MEAS}
    max_block_by = {k: max(completed[k], (active[k] if active else 0)) for k in MEAS}
    max_block = max_block_by["all"]  # back-compat (terminal claudetop.py)

    current_block = None
    if active:
        elapsed_min = max((now_ts - active["start"]) / 60, 1)
        current_block = {
            **active,
            "minutes_left": max(0, int((active["end"] - now_ts) / 60)),
            "burn_per_min": int(active["tokens"] / elapsed_min),
            "pct_of_max": round(100 * active["tokens"] / max_block, 1) if max_block else 0,
        }

    # --- cumulative token curve within the current 5-hour window ---
    # x = time (window start -> reset), y = cumulative tokens; ceiling per measure
    # = the peak of past *completed* windows for that measure.
    window_series = None
    if active:
        ws, we = active["start"], active["end"]
        ceil = {k: (completed[k] or active[k] or 1) for k in MEAS}
        cum = {k: 0 for k in MEAS}
        pts = []
        for e in entries:
            if e["t"] < ws:
                continue
            if e["t"] >= we:
                break
            io = e["in"] + e["out"]
            cw = e["cw5"] + e["cw1"]
            cum["all"] += io + e["cr"] + cw
            cum["processed"] += io + cw
            cum["content"] += io
            cum["output"] += e["out"]
            pts.append({"t": e["t"], **{k: cum[k] for k in MEAS}})
        window_series = {"start": ws, "end": we, "now": now_ts,
                         "max": dict(ceil), "used": dict(cum), "points": pts}

    # --- date-range filter for history section ---
    if days:
        cutoff = (now - timedelta(days=days - 1)).replace(
            hour=0, minute=0, second=0, microsecond=0).timestamp()
        filtered = [e for e in entries if e["t"] >= cutoff]
    else:
        filtered = entries

    daily = defaultdict(lambda: {"agg": zero(), "models": defaultdict(int),
                                 "sessions": set()})
    models = defaultdict(lambda: {"agg": zero(), "sessions": set()})
    projects = defaultdict(lambda: {"agg": zero(), "last": 0, "sessions": set()})
    heatmap = [[0] * 24 for _ in range(7)]  # [weekday][hour], Mon=0
    tools = defaultdict(int)
    total = zero()
    cache = {"read": 0, "w5": 0, "w1": 0, "uncached_in": 0}
    side_tokens = 0
    sessions_all = set()

    for e in filtered:
        dt = datetime.fromtimestamp(e["t"]).astimezone()
        day = dt.strftime("%Y-%m-%d")
        tok = e["in"] + e["out"] + e["cr"] + e["cw5"] + e["cw1"]
        d = daily[day]
        add(d["agg"], e)
        d["models"][e["model"]] += tok
        if e["session"]:
            d["sessions"].add(e["session"])
            sessions_all.add(e["session"])
        m = models[e["model"]]
        add(m["agg"], e)
        if e["session"]:
            m["sessions"].add(e["session"])
        p = projects[e["project"]]
        add(p["agg"], e)
        p["last"] = max(p["last"], e["t"])
        if e["session"]:
            p["sessions"].add(e["session"])
        heatmap[dt.weekday()][dt.hour] += tok
        for t in e["tools"]:
            tools[t] += 1
        add(total, e)
        cache["read"] += e["cr"]
        cache["w5"] += e["cw5"]
        cache["w1"] += e["cw1"]
        cache["uncached_in"] += e["in"]
        if e["side"]:
            side_tokens += tok

    def agg_out(a):
        return {"in": a["in"], "out": a["out"], "cr": a["cr"], "cw": a["cw"],
                "total": total_tokens(a), "cost": round(a["cost"], 4),
                "msgs": a["msgs"]}

    daily_out = [
        {"date": day, **agg_out(v["agg"]),
         "models": {MODEL_LABELS.get(m, m): t for m, t in v["models"].items()},
         "sessions": len(v["sessions"])}
        for day, v in sorted(daily.items())
    ]
    models_out = {
        MODEL_LABELS.get(m, m): {**agg_out(v["agg"]), "sessions": len(v["sessions"])}
        for m, v in models.items() if m != "<synthetic>"
    }
    projects_out = {
        name: {**agg_out(v["agg"]), "last": v["last"], "sessions": len(v["sessions"])}
        for name, v in projects.items()
    }

    snapshot_history(entries)  # persist daily history (throttled, best-effort)

    today = daily.get(today_key)
    first_ts = entries[0]["t"] if entries else now_ts
    return {
        "generated_at": now_ts,
        "days": days,
        "since": first_ts,
        "today": ({**agg_out(today["agg"]), "sessions": len(today["sessions"])}
                  if today else {**agg_out(zero()), "sessions": 0}),
        "current_block": current_block,
        "window_series": window_series,
        "max_block": max_block,
        "max_block_by": max_block_by,
        "blocks": block_list[-24:],
        "blocks_total": len(block_list),
        "daily": daily_out,
        "models": models_out,
        "projects": projects_out,
        "heatmap": heatmap,
        "tools": dict(sorted(tools.items(), key=lambda kv: -kv[1])[:14]),
        "totals": {**agg_out(total), "sessions": len(sessions_all),
                   "side_tokens": side_tokens},
        "cache": cache,
    }


# ---------------------------------------------------------------- server ----

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/data":
            q = parse_qs(parsed.query)
            days = q.get("days", [None])[0]
            days = int(days) if days and days.isdigit() else None
            body = json.dumps(build_payload(days)).encode()
            self._send(200, "application/json", body)
        else:
            pages = {
                "/": "index.html", "/index.html": "index.html",
                "/taskman": "taskman.html", "/taskman.html": "taskman.html",
                "/activity": "taskman.html",
            }
            fname = pages.get(parsed.path)
            if fname:
                path = os.path.join(os.path.dirname(os.path.abspath(__file__)), fname)
                with open(path, "rb") as fh:
                    self._send(200, "text/html; charset=utf-8", fh.read())
            else:
                self._send(404, "text/plain", b"not found")

    def _send(self, code, ctype, body):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


def main():
    server = HTTPServer(("127.0.0.1", PORT), Handler)
    print(f"Claude Stats — dashboard : http://localhost:{PORT}/")
    print(f"              task manager: http://localhost:{PORT}/taskman")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
