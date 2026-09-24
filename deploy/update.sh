#!/usr/bin/env bash
# Bring the server to the newest code on GitHub and restart the services.
#   bash ~/ta-screener/deploy/update.sh
# ta-live is restarted too: a nightly update in progress stops and runs again later.
set -euo pipefail
APP="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export PATH="$HOME/.local/bin:$PATH"
git -C "$APP" pull --ff-only
uv pip install --python "$APP/.venv/bin/python" -r "$APP/requirements.txt"
sudo systemctl restart ta-web
if systemctl is-enabled --quiet ta-live && systemctl is-active --quiet ta-live; then
    sudo systemctl restart ta-live
fi
systemctl --no-pager --lines=0 status ta-web ta-live | grep -E "^●|Active:"
