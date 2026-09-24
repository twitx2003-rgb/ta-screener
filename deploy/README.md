# Running ta-screener on a server

Owner's decision (2026-09-24): an Oracle Cloud **Always Free** VM, so the site, the live
quotes and the channels keep running with the home computer off. Personal use; the
TradingView data stays under the owner's account, as on the home computer.

## 1. The VM (Oracle Cloud console, done by the owner)

- Compute → Instances → Create instance: **Canonical Ubuntu 24.04**, shape
  **VM.Standard.A1.Flex** with 4 OCPU / 24 GB (Always Free-eligible), a public IPv4
  address, and the owner's SSH **public** key (the private key stays on the owner's
  computer: `%USERPROFILE%\.ssh\oracle_ta`).
- Networking → the VCN → Security Lists → Default: ingress TCP **80** and **443** from
  `0.0.0.0/0`.
- Host name without buying a domain: `<ip with dashes>.sslip.io` (for 1.2.3.4:
  `1-2-3-4.sslip.io`) resolves to the VM, and Caddy gets a certificate for it.

## 2. Install (on the VM)

```
ssh -i ~/.ssh/oracle_ta ubuntu@<ip>
git clone https://github.com/twitx2003-rgb/ta-screener.git
bash ta-screener/deploy/setup.sh <ip-with-dashes>.sslip.io
```

## 3. Sign-ins (the owner, once)

- **TradingView:** sign in on tradingview.com in your own browser first. Then connect
  with the sign-in port forwarded, so TradingView's redirect reaches the server:
  ```
  ssh -i ~/.ssh/oracle_ta -L 8766:localhost:8766 ubuntu@<ip>
  cd ta-screener && .venv/bin/python run.py --auth-tradingview
  ```
  Open the printed link in your browser and approve.
- **Claude Code (your subscription):** on the VM run `claude`, choose the subscription
  login, open the link on your computer and paste the code back; then `/exit`.
  No API key is set on the server, so the channels use the subscription.

## 4. First data, then the services

```
cd ~/ta-screener
tmux new -s first            # survives a dropped SSH connection (detach: Ctrl+B, D)
.venv/bin/python run.py --update             # universe + bars + scan, about an hour
.venv/bin/python run.py --backfill-outcomes  # past breakouts, a few hours
sudo systemctl start ta-live
```

## Everyday

- Newest code: `bash ~/ta-screener/deploy/update.sh`
- Logs: `journalctl -u ta-live -f`, `journalctl -u ta-web -f`, and `~/ta-screener/logs/`
- Status: `systemctl status ta-web ta-live caddy`
