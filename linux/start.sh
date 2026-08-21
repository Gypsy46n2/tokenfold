#!/usr/bin/env bash
# TokenFold — start the proxy (foreground). Usage: ./start.sh [port] [upstream]
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PORT="${1:-9339}"
UPSTREAM="${2:-}"
ARGS=(-m tokenfold.cli serve --port "$PORT")
[ -n "$UPSTREAM" ] && ARGS+=(--upstream "$UPSTREAM")
echo "TokenFold proxy -> http://localhost:$PORT/v1   (Ctrl+C to stop)"
exec "$HERE/.venv/bin/python" "${ARGS[@]}"
