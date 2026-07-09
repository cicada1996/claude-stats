#!/usr/bin/env python3
"""Claude Stats — native desktop app.

Opens the Activity Monitor UI in its own macOS window (system WebKit view).
No web browser, no localhost, no server — data is served in-process to the
page through a Python bridge.

    python3 app.py

or double-click "Claude Stats.app".
"""

import os
import sys

os.chdir(os.path.dirname(os.path.abspath(__file__)))

try:
    import webview
except ImportError:
    # First-run convenience: install the native-window library, then retry.
    import subprocess
    print("Installing the window library (pywebview) — one-time setup…")
    subprocess.run([sys.executable, "-m", "pip", "install", "--user", "pywebview"],
                   check=False)
    import webview

import server  # data engine (importing does not start a web server)


class Api:
    """Exposed to the page as window.pywebview.api — the whole data channel."""

    def get_data(self, days):
        return server.build_payload(days)

    def get_config(self):
        return server.load_config()

    def set_config(self, skin, palette):
        """Persist appearance settings. Invalid values are ignored, and the
        config as actually stored is returned so the page can resync."""
        return server.save_config(skin=skin, palette=palette)


def main():
    with open("taskman.html", "r", encoding="utf-8") as fh:
        html = fh.read()
    # Tell the page it's running as a native app (use the bridge, not fetch).
    html = html.replace("<head>", "<head><script>window.__APP__=true;</script>", 1)

    webview.create_window(
        "Claude Stats",
        html=html,
        js_api=Api(),
        width=1200,
        height=820,
        min_size=(300, 180),
    )
    webview.start()


if __name__ == "__main__":
    main()
