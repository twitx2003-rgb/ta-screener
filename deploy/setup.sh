#!/usr/bin/env bash
# One-time setup of ta-screener on a fresh Ubuntu 24.04 server (built for Oracle Cloud's
# Always Free Ampere A1 VM). Run as the default user (ubuntu), from anywhere:
#
#   git clone https://github.com/twitx2003-rgb/ta-screener.git
#   bash ta-screener/deploy/setup.sh <public host name>      # e.g. 1-2-3-4.sslip.io
#
# It installs Python 3.14 (uv), the project's packages, Claude Code, and Caddy (HTTPS
# in front of the site, which itself only listens on 127.0.0.1:8050), opens ports 80
# and 443 in the VM's own firewall, writes config.local.yaml (this server's host name)
# and two services: ta-web (the site) and ta-live (quotes, the nightly update, the
# channels). Nothing secret is written: the TradingView and Claude sign-ins are done
# afterwards, by the owner (see deploy/README.md). Safe to run again.
set -euo pipefail

HOST_NAME="${1:?usage: bash deploy/setup.sh <public host name, e.g. 1-2-3-4.sslip.io>}"
APP="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUN_AS="$(id -un)"
BIN="$HOME/.local/bin"
export PATH="$BIN:$PATH"

echo "== system packages"
sudo apt-get update -q
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -q git curl ca-certificates caddy \
    iptables-persistent tmux

echo "== Python 3.14 and the project's packages (uv)"
command -v uv >/dev/null || curl -LsSf https://astral.sh/uv/install.sh | sh
uv python install 3.14
[ -x "$APP/.venv/bin/python" ] || uv venv --python 3.14 "$APP/.venv"
uv pip install --python "$APP/.venv/bin/python" -r "$APP/requirements.txt"

echo "== Claude Code (the channels' writer, signed in to your subscription later)"
command -v claude >/dev/null || curl -fsSL https://claude.ai/install.sh | bash

echo "== this server's settings: config.local.yaml"
cat > "$APP/config.local.yaml" <<EOF
# This server only (gitignored). Written by deploy/setup.sh.
web:
  open_browser: false
  public_hosts: ["$HOST_NAME"]
EOF

echo "== HTTPS in front of the site (Caddy gets the certificate by itself)"
sudo tee /etc/caddy/Caddyfile >/dev/null <<EOF
$HOST_NAME {
    encode gzip
    reverse_proxy 127.0.0.1:8050
}
EOF
sudo systemctl enable caddy >/dev/null
sudo systemctl reload-or-restart caddy

echo "== open ports 80 and 443 (Oracle's Ubuntu image rejects everything but SSH)"
for port in 80 443; do
    sudo iptables -C INPUT -p tcp --dport "$port" -m state --state NEW -j ACCEPT 2>/dev/null \
        || sudo iptables -I INPUT 1 -p tcp --dport "$port" -m state --state NEW -j ACCEPT
done
sudo netfilter-persistent save >/dev/null

echo "== services"
for name in web live; do
    sudo tee "/etc/systemd/system/ta-$name.service" >/dev/null <<EOF
[Unit]
Description=ta-screener $name
After=network-online.target
Wants=network-online.target

[Service]
User=$RUN_AS
WorkingDirectory=$APP
ExecStart=$APP/.venv/bin/python run.py --$( [ "$name" = web ] && echo serve || echo live )
Environment=PYTHONUNBUFFERED=1
Environment=PATH=$BIN:/usr/local/bin:/usr/bin:/bin
Restart=always
RestartSec=30

[Install]
WantedBy=multi-user.target
EOF
done
sudo systemctl daemon-reload
sudo systemctl enable --now ta-web
sudo systemctl enable ta-live          # started after the sign-ins (deploy/README.md)

echo
echo "Done. The site: https://$HOST_NAME/ (empty until the first update)."
echo "Next: the sign-ins and the first update, as in deploy/README.md."
