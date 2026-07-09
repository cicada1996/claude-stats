# Design: switchable app design (skins + palettes)

**Status:** implemented.
**Date:** 2026-07-09

## What we had

The project shipped two unrelated renderers, and the native app only ever loaded one.

| | `taskman.html` (native app) | `index.html` (browser) |
|---|---|---|
| Rendering | one `<div>`, `white-space: pre`, character grid | real DOM + SVG |
| Graph | braille dots (U+2800), 2×4 subpixels per cell | SVG |
| Color | 9 hardcoded hex greens | CSS custom properties, light + dark |

The style in `taskman.html` is a **TUI** — character-cell art drawn with braille dots,
box-drawing characters and block elements, in a green **phosphor** palette. Same genre
as `htop`, `btop`, `glances`.

## What changed from the original proposal

The first draft proposed making the app *load `index.html`* for the clean look, since
a clean design already existed there. **That was rejected once the requirement became
"every version shows the same information, with the graph as the main feature."**

`index.html` is a different dashboard, not a restyle: it has stat tiles, a daily bar
chart, a heatmap and day-range filters, but **no cumulative 5-hour-window graph**.
Loading it would have swapped the app's centrepiece for a different set of numbers.

So instead: **both skins live inside `taskman.html`**, driven by the same payload and
the same derivation function. Parity is structural rather than something to maintain
by hand. `index.html` is untouched and stays the deeper browser analytics view.

This also removed a risk the original design carried: `install-app.command` would
have needed to copy `index.html` into the bundle, and would have 404'd after install
if anyone forgot. With one file, the install script needed no change.

## Shape

    server.py    load_config() / save_config()  → ~/.claude-stats/config.json
    app.py       Api.get_config() / Api.set_config(skin, palette)
    taskman.html windowStats()  ← single source of the numbers
                   ├─ rasterGraph()  → braille    (terminal skin)
                   └─ svgGraph()     → SVG        (clean skin)

`windowStats(ws, mode, now)` computes used / ceiling / burn / projection / `yAt(x)`.
Both renderers sample the identical `yAt` curve, so the two graphs cannot disagree.

### Why config lives in Python, not the page

pywebview's `webview.start()` defaults to `private_mode=True`, which **clears
`localStorage` on every launch**. A theme stored in the page would silently reset each
time the app opened. `server.py` already owns `~/.claude-stats/`, so config goes there.
The browser build has no bridge and falls back to `localStorage`, where it does persist.

## Skins and palettes

- **terminal** (default) — the TUI, unchanged in layout. Palettes:
  - `phosphor` — the original green. Default.
  - `amber` — DEC/VT100 amber on black.
  - `slate` — modern emulator: low-contrast cool grays, `text-shadow: none`.
- **clean** — DOM + SVG. Filled area under the real curve, dashed projection (red when
  on pace to pass peak), dashed peak line, hourly ticks, `now` marker. Follows the
  system light/dark preference. Palette selector is hidden here, since it doesn't apply.

The glow is a `text-shadow` on `.ln`, so it became a per-palette variable.

## Error handling

- Unknown / corrupt `skin` or `palette` fall back to `terminal` / `phosphor`.
  `save_config` validates before writing, so a bad bridge call cannot wedge the app.
- Appearance clicks are ignored until `loadConfig()` resolves (`state.ready`). Without
  this, clicking during the boot gap would persist the in-memory defaults over the
  stored settings.
- `refresh()` failures render into whichever skin is visible. Previously the
  "disconnected" message was written into `#term`, which is `display:none` under the
  clean skin — a dead server would have left the clean view silently stale.
- The settings row renders in the empty state too, so it is impossible to get stuck in
  a skin you dislike while no window is active.

## Verification performed

- Headless harness (`vm` + stub DOM) driving **both renderers against the real payload**:
  43 checks — section presence, no braille leaking into the clean skin, and figure-level
  parity (same `used`, `peak`, `today`, top model, top project) across all four count
  modes.
- Empty state (`window_series: null`) in both skins: no crash, no `NaN`, settings reachable.
- End-to-end in the real pywebview/WebKit window via `evaluate_js`: booted from stored
  config, clicked through skins and palettes, asserted computed styles
  (`amber` → `rgb(255,176,0)`, `slate` → `text-shadow: none`), zero console errors.
  Repeated across relaunches to confirm the setting survives.
- `install-app.command` → confirmed the installed bundle carries the new files.
- `python3 -S -c "import server"` still passes: config handling added no dependency.

## Deliberately out of scope

- `claudetop.py` (curses) keeps its ANSI colors.
- `index.html` keeps its own `prefers-color-scheme` handling.
