#!/bin/bash
# Serve the tracker dashboard on http://127.0.0.1:8765/ (Ctrl-C to stop).
set -euo pipefail
cd "$(dirname "$0")"
PORT="${PORT:-8765}"
echo "Tracker dashboard: http://127.0.0.1:${PORT}/"
exec python3 -m http.server "$PORT" --bind 127.0.0.1
