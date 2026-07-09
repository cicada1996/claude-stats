#!/usr/bin/env python3
"""claudetop — a live terminal dashboard for Claude Code token usage.

Runs entirely in your terminal (no browser). Reuses the data engine in
server.py. Keys:  7 / 3 / 9 / 0  change the history range,  r  refreshes,
q  (or Esc) quits.

    python3 claudetop.py
"""

import os
import sys
import time
import shutil

import server  # data engine (importing does not start the web server)

# ---------------------------------------------------------------- styling ---

RESET = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"


def fg(n):
    return f"\033[38;5;{n}m"


ACCENT = 39      # blue      — Fable 5
C_AQUA = 37      # teal      — Opus 4.8
C_YELL = 214     # amber     — Opus 4.7
C_GREEN = 34     # green     — Sonnet 4.6
C_VIOL = 99      # violet
C_RED = 203
C_TRACK = 238    # bar track
C_MUTED = 245
GOOD, WARN, CRIT = 41, 214, 203

MODEL_COLORS = {
    "Fable 5": ACCENT, "Opus 4.8": C_AQUA, "Opus 4.7": C_YELL,
    "Sonnet 4.6": C_GREEN, "Sonnet 5": C_VIOL, "Sonnet 4.5": C_VIOL,
    "Haiku 4.5": 168,
}
ORDER = ["Fable 5", "Opus 4.8", "Opus 4.7", "Sonnet 4.6", "Sonnet 5",
         "Sonnet 4.5", "Haiku 4.5"]


def model_sort(names):
    return sorted(names, key=lambda n: ORDER.index(n) if n in ORDER else 99)


def mcolor(name):
    return MODEL_COLORS.get(name, C_MUTED)


# ---------------------------------------------------------------- format ----

def fmt(n):
    n = float(n)
    if n >= 1e9:
        return f"{n/1e9:.1f}B"
    if n >= 1e6:
        return f"{n/1e6:.1f}M"
    if n >= 1e3:
        return f"{n/1e3:.1f}K"
    return str(int(round(n)))


def money(n):
    n = float(n)
    if n >= 100:
        return "$" + f"{n:,.0f}"
    return f"${n:.2f}"


# ---------------------------------------------------------- line builder ----

class Line:
    """A single output line that tracks *visible* width, so ANSI codes don't
    throw off padding/alignment."""

    def __init__(self):
        self.buf = ""
        self.w = 0

    def txt(self, s, color="", width=None, right=False):
        if width is not None:
            s = s[:width]
            s = s.rjust(width) if right else s.ljust(width)
        self.buf += (color + s + RESET) if color else s
        self.w += len(s)
        return self

    def raw(self, s, vis):
        self.buf += s
        self.w += vis
        return self

    def pad(self, W):
        if self.w < W:
            self.buf += " " * (W - self.w)
            self.w = W
        return self

    def __str__(self):
        return self.buf


# ---------------------------------------------------------------- bars ------

EIGHTHS = " ▏▎▍▌▋▊▉█"
LEVELS = " ▁▂▃▄▅▆▇█"


def meter(width, frac, color):
    """A horizontal fill meter with sub-cell precision. Returns (str, vis)."""
    frac = max(0.0, min(1.0, frac))
    fillf = width * frac
    full = int(fillf)
    s = fg(color) + "█" * full
    used = full
    if full < width:
        idx = int(round((fillf - full) * 8))
        if idx > 0:
            s += fg(color) + EIGHTHS[idx]
            used += 1
    s += fg(C_TRACK) + "░" * (width - used) + RESET
    return s, width


def seg_bar(width, value, maxv, models):
    """Bar length ∝ value/maxv, split into colored segments by model share."""
    fill = int(round(width * value / maxv)) if maxv > 0 else 0
    fill = max(0, min(width, fill))
    s = ""
    if fill > 0 and value > 0 and models:
        names = model_sort([n for n in models if models[n] > 0])
        props = [models[n] / value * fill for n in names]
        cells = [int(p) for p in props]
        rem = fill - sum(cells)
        order = sorted(range(len(names)), key=lambda i: props[i] - cells[i],
                       reverse=True)
        for i in order[:rem]:
            cells[i] += 1
        for i, n in enumerate(names):
            if cells[i] > 0:
                s += fg(mcolor(n)) + "█" * cells[i]
    s += fg(C_TRACK) + "░" * (width - fill) + RESET
    return s, width


def bar1(width, value, maxv, color):
    fill = int(round(width * value / maxv)) if maxv > 0 else 0
    fill = max(0, min(width, fill))
    return fg(color) + "█" * fill + fg(C_TRACK) + "░" * (width - fill) + RESET, width


def spark(values, maxv, active_idx):
    s = ""
    for i, v in enumerate(values):
        lvl = 0 if (maxv <= 0 or v <= 0) else max(1, int(round(v / maxv * 8)))
        col = fg(ACCENT) if i == active_idx else fg(31)
        s += col + LEVELS[lvl]
    return s + RESET, len(values)


# ---------------------------------------------------------------- render ----

def rule(W, title, color=ACCENT):
    cs = fg(color)
    l = Line().txt("── ", cs).txt(title, BOLD + cs).txt(" ")
    l.txt("─" * max(0, W - l.w), DIM)
    return str(l.pad(W))


_alltime = {"t": 0.0, "total": 0, "cost": 0.0}


def alltime():
    now = time.time()
    if now - _alltime["t"] > 60:
        p = server.build_payload(None)
        _alltime.update(t=now, total=p["totals"]["total"], cost=p["totals"]["cost"])
    return _alltime["total"], _alltime["cost"]


def render(payload, days, W, R):
    lines = []

    def emit(s=""):
        lines.append(s)

    def left():
        return R - len(lines) - 1  # keep one row for footer

    today = payload["today"]
    at_total, at_cost = alltime()

    # ---- title bar ----
    rng = {7: "7 days", 30: "30 days", 90: "90 days", None: "all time"}[days]
    clock = time.strftime("%H:%M:%S")
    t = Line()
    t.txt(" ▐ ", fg(ACCENT)).txt("CLAUDE STATS", BOLD + fg(ACCENT)).txt(" ▌ ", fg(ACCENT))
    t.txt("token usage", DIM)
    t.pad(W - len(clock) - 1).txt(clock, fg(C_MUTED)).txt(" ")
    emit(str(t))
    emit()

    # ---- stat tiles ----
    hit_base = today["cr"] + today["cw"] + today["in"]
    hit = round(100 * today["cr"] / hit_base) if hit_base else 0
    tiles = [
        ("TOKENS TODAY", fmt(today["total"]),
         f"{fmt(today['out'])} gen · {fmt(today['cr'] + today['cw'])} cache", ACCENT),
        ("COST TODAY (API-EQV)", money(today["cost"]),
         f"{today['msgs']} responses · {today['sessions']} sess", C_AQUA),
        ("ALL-TIME TOKENS", fmt(at_total), f"≈ {money(at_cost)} at API prices", C_YELL),
        ("CACHE HIT TODAY", f"{hit}%", "served from cache", C_GREEN),
    ]
    tw = W // 4
    lab, val, sub = Line(), Line(), Line()
    for i, (L, V, S, col) in enumerate(tiles):
        w = tw if i < 3 else W - tw * 3
        lab.txt(L, DIM, w)
        val.txt(V, BOLD + fg(col), w)
        sub.txt(S, fg(C_MUTED), w)
    emit(str(lab)); emit(str(val)); emit(str(sub)); emit()

    # ---- current 5-hour window ----
    emit(rule(W, "CURRENT 5-HOUR WINDOW"))
    cb = payload["current_block"]
    if cb and time.time() >= cb["end"]:
        cb = None  # window elapsed while idle — no rebuild has happened yet
    if cb:
        now2 = time.time()
        elapsed_min = max((now2 - cb["start"]) / 60, 1)
        cb = dict(cb, minutes_left=max(0, int((cb["end"] - now2) / 60)),
                  burn_per_min=int(cb["tokens"] / elapsed_min))
        pct = min(100, cb["pct_of_max"])
        col = CRIT if pct >= 90 else WARN if pct >= 70 else ACCENT
        mw = max(10, W - 34)
        bar, _ = meter(mw, pct / 100, col)
        ln = Line().txt("  ").raw(bar, mw).txt("  ")
        ln.txt(f"{pct:.0f}%", BOLD + fg(col))
        emit(str(ln.pad(W)))
        left_h, left_m = cb["minutes_left"] // 60, cb["minutes_left"] % 60
        info = Line().txt("  ")
        info.txt(fmt(cb["tokens"]) + " tokens", BOLD)
        info.txt(f"  of {fmt(payload['max_block'])} peak", fg(C_MUTED))
        info.txt(f"   resets in {left_h}h {left_m:02d}m", fg(C_MUTED))
        info.txt(f"   {fmt(cb['burn_per_min'])}/min", fg(C_MUTED))
        info.txt(f"   {money(cb['cost'])}", fg(C_AQUA))
        emit(str(info.pad(W)))
    else:
        emit(str(Line().txt("  no active window — starts with your next message",
                            fg(C_MUTED)).pad(W)))
    emit()

    # ---- recent windows ----
    blocks = payload["blocks"]
    if left() > 3 and blocks:
        emit(rule(W, "RECENT 5-HOUR WINDOWS"))
        vals = [b["tokens"] for b in blocks]
        active_idx = next((i for i, b in enumerate(blocks) if b["active"]), -1)
        maxv = payload["max_block"]
        sp, vis = spark(vals, maxv, active_idx)
        ln = Line().txt("  ").raw(sp, vis)
        ln.txt(f"   peak {fmt(maxv)}", fg(C_MUTED))
        emit(str(ln.pad(W)))
        emit()

    # ---- tokens per day ----
    daily = payload["daily"]
    if left() > 3 and daily:
        emit(rule(W, f"TOKENS PER DAY  ·  {rng}"))
        avail = left() - 1  # leave room for model/project sections + blank
        show = daily[-max(1, min(len(daily), avail - 10)):] if avail > 12 \
            else daily[-max(1, avail):]
        maxd = max((d["total"] for d in show), default=1)
        bw = max(8, W - 30)
        for d in reversed(show):
            if left() <= 1:
                break
            label = d["date"][5:]  # MM-DD
            bar, _ = seg_bar(bw, d["total"], maxd, d["models"])
            ln = Line().txt("  ").txt(label, fg(C_MUTED), 6).txt(" ").raw(bar, bw)
            ln.txt(" ").txt(fmt(d["total"]), BOLD, 7, right=True)
            ln.txt(" ").txt(money(d["cost"]), fg(C_AQUA), 8, right=True)
            emit(str(ln.pad(W)))
        emit()

    # ---- by model ----
    models = payload["models"]
    if left() > 3 and models:
        emit(rule(W, "BY MODEL"))
        rows = sorted(models.items(), key=lambda kv: -kv[1]["total"])
        maxm = max((m["total"] for _, m in rows), default=1)
        bw = max(8, W - 40)
        for name, m in rows:
            if left() <= 1:
                break
            bar, _ = bar1(bw, m["total"], maxm, mcolor(name))
            ln = Line().txt("  ").txt("█ ", fg(mcolor(name))).txt(name, "", 11)
            ln.raw(bar, bw).txt(" ").txt(fmt(m["total"]), BOLD, 7, right=True)
            ln.txt(" ").txt(money(m["cost"]), fg(C_AQUA), 8, right=True)
            emit(str(ln.pad(W)))
        emit()

    # ---- by project ----
    projects = payload["projects"]
    if left() > 2 and projects:
        emit(rule(W, "BY PROJECT"))
        rows = sorted(projects.items(), key=lambda kv: -kv[1]["total"])
        maxp = max((p["total"] for _, p in rows), default=1)
        bw = max(8, W - 40)
        for name, p in rows:
            if left() <= 1:
                break
            bar, _ = bar1(bw, p["total"], maxp, 31)
            ln = Line().txt("  ").txt(name[:13], fg(C_MUTED), 13).txt(" ")
            ln.raw(bar, bw).txt(" ").txt(fmt(p["total"]), BOLD, 7, right=True)
            ln.txt(" ").txt(money(p["cost"]), fg(C_AQUA), 8, right=True)
            emit(str(ln.pad(W)))

    # pad body to fill screen
    while len(lines) < R - 1:
        lines.append("")

    # ---- footer ----
    def keyseg(k, label, on):
        col = BOLD + fg(ACCENT) if on else fg(C_MUTED)
        return f"{col}[{k}]{RESET}{fg(C_MUTED)} {label}{RESET}", 4 + len(label)

    f = Line().txt("  ")
    for k, lb, on in [("7", "7d", days == 7), ("3", "30d", days == 30),
                      ("9", "90d", days == 90), ("0", "all", days is None)]:
        s, v = keyseg(k, lb, on)
        f.raw(s, v).txt("  ")
    f.raw(*keyseg("r", "refresh", False)).txt("  ")
    f.raw(*keyseg("q", "quit", False))
    upd = "updated " + time.strftime("%H:%M:%S")
    f.pad(W - len(upd) - 1).txt(upd, DIM)
    lines.append(str(f))

    return "\r\n".join(l for l in lines)


# ---------------------------------------------------------------- input -----

def run_tty():
    import termios
    import tty
    import select

    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    sys.stdout.write("\033[?1049h\033[?25l")  # alt screen + hide cursor
    sys.stdout.flush()
    days = 30
    payload = None
    last_sig = None
    try:
        tty.setcbreak(fd)
        while True:
            # Rebuild only when a transcript file actually changed; otherwise
            # reuse the payload and just re-render (clock + window countdown).
            sig = server.signature()
            if payload is None or sig != last_sig:
                payload = server.build_payload(days)
                last_sig = sig
            cols, rows = shutil.get_terminal_size((100, 30))
            W = max(50, min(cols, 130))
            frame = render(payload, days, W, rows)
            sys.stdout.write("\033[H" + frame + "\033[J")
            sys.stdout.flush()

            r, _, _ = select.select([sys.stdin], [], [], 1.0)
            if not r:
                continue
            k = sys.stdin.read(1)
            if k in ("q", "\x1b", "\x03"):
                break
            elif k == "7":
                days, payload = 7, None
            elif k == "3":
                days, payload = 30, None
            elif k == "9":
                days, payload = 90, None
            elif k in ("0", "a"):
                days, payload = None, None
            elif k == "r":
                payload = None
    except KeyboardInterrupt:
        pass
    finally:
        sys.stdout.write("\033[?25h\033[?1049l")
        sys.stdout.flush()
        termios.tcsetattr(fd, termios.TCSADRAIN, old)


def main():
    if not sys.stdout.isatty():
        # Non-interactive (piped/redirected): print one plain frame.
        payload = server.build_payload(30)
        text = render(payload, 30, 100, 44)
        import re
        print(re.sub(r"\033\[[0-9;?]*[A-Za-z]", "", text))
        return
    run_tty()


if __name__ == "__main__":
    main()
