#!/usr/bin/env bash
# TokenFold — start detached; logs to tokenfold.log next to this script
# Usage: ./start-background.sh [port] [upstream]
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PORT="${1:-9339}"
UPSTREAM="${2:-}"
if [ -f "$HERE/tokenfold.pid" ] && kill -0 "$(cat "$HERE/tokenfold.pid")" 2>/dev/null; then
    echo "TokenFold already running (pid $(cat "$HERE/tokenfold.pid")). Stop it first: ./stop.sh"
    exit 1
fi
ARGS=(-m tokenfold.cli serve --port "$PORT")
[ -n "$UPSTREAM" ] && ARGS+=(--upstream "$UPSTREAM")
setsid nohup "$HERE/.venv/bin/python" "${ARGS[@]}" \
    > "$HERE/tokenfold.log" 2>&1 &
echo $! > "$HERE/tokenfold.pid"
echo "TokenFold running in background on http://localhost:$PORT/v1 (pid $(cat "$HERE/tokenfold.pid"))"
[ -n "$UPSTREAM" ] && echo "Upstream: $UPSTREAM"
echo "Stop it with: ./stop.sh"
