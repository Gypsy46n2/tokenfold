#!/usr/bin/env bash
# TokenFold — stop the background proxy
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [ -f "$HERE/tokenfold.pid" ]; then
    kill "$(cat "$HERE/tokenfold.pid")" 2>/dev/null && echo "TokenFold stopped."
    rm -f "$HERE/tokenfold.pid"
else
    pkill -f "tokenfold.cli serve" && echo "TokenFold stopped." || echo "Not running."
fi
