#!/bin/bash
# Double-click to open the Claude Stats terminal dashboard.
cd "$(dirname "$0")"
exec python3 claudetop.py
