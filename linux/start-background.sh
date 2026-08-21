#!/usr/bin/env bash
# TokenFold — start detached; logs to tokenfold.log next to this script
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PORT="${1:-9339}"
nohup "$HERE/.venv/bin/python" -m tokenfold.cli serve --port "$PORT" \
    > "$HERE/tokenfold.log" 2>&1 &
echo $! > "$HERE/tokenfold.pid"
echo "TokenFold running in background on http://localhost:$PORT/v1 (pid $(cat "$HERE/tokenfold.pid"))"
echo "Stop it with: ./stop.sh"
