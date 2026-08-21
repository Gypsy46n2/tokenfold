#!/usr/bin/env bash
# TokenFold — Linux installer
# Creates an isolated venv next to this script and installs the core package.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CORE="$(dirname "$HERE")/core"
[ -d "$CORE" ] || { echo "core/ not found next to linux/ - keep the folder structure intact"; exit 1; }

echo "Creating venv..."
python3 -m venv "$HERE/.venv"
"$HERE/.venv/bin/pip" install --upgrade pip -q
echo "Installing TokenFold core..."
"$HERE/.venv/bin/pip" install -e "$CORE" -q

echo
echo "Done. Start the proxy with:  ./start.sh"
echo "Dashboard (once running):    http://localhost:9339/tokenfold/dashboard"
