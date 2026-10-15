# Deploy: Oracle Cloud Always Free (Singapore)

Why Oracle: as of 2026-10 it is the only option found with an always-on VM **and** a persistent disk at no cost
(the in-process rain poller and SQLite rule out sleep-on-idle PaaS free tiers; Fly.io now gives new accounts
only a short trial). Oracle requires a card for identity verification; Always Free resources are not billed.
Free-tier terms change: re-check https://docs.oracle.com/en-us/iaas/Content/FreeTier/resourceref.htm before relying on them.

`deploy/setup.sh` is syntax-checked only; it has not been run on a real VM yet.

## One-time (owner, in the browser)
1. Sign up at cloud.oracle.com. **Home region = Singapore (ap-singapore-1)**; it cannot be changed later.
2. Compute → Create instance: image *Canonical Ubuntu 24.04*, shape **VM.Standard.E2.1.Micro** (Always Free, 1 GB RAM;
   A1 Flex also works but often reports "out of capacity"). Download the SSH private key.
3. Networking → the instance's subnet → Security List → add ingress TCP **80** and **443** from 0.0.0.0/0.
4. Note the public IP. Domain without buying one: `<ip-with-dashes>.sslip.io` (e.g. `203-0-113-7.sslip.io`).
   If Let's Encrypt refuses it, use a free DuckDNS subdomain instead.

## Install / update (from this repo on Windows, committed HEAD only)
```powershell
powershell -File deploy/push.ps1 -Server ubuntu@<ip> -Domain <ip-with-dashes>.sslip.io -Key <path-to-key>
```
First run creates `/etc/floodwatch.env` from `deploy/floodwatch.env.example`. Fill it on the server
(`sudo nano /etc/floodwatch.env`), then `sudo systemctl restart floodwatch`. Re-running push.ps1 updates the code;
the database in `/var/lib/floodwatch` and the env file are kept. Previous release stays in `/opt/floodwatch.old`.

## LINE console
- Messaging API channel → Webhook URL `https://<domain>/webhook/line`, enable "Use webhook", **leave webhook
  redelivery off** (redelivered events are skipped anyway).
- Rich menu: on the server, `cd /opt/floodwatch && sudo -u floodwatch bash -c 'set -a; . /etc/floodwatch.env; .venv/bin/python -m src.alerts.rich_menu'` (installs the 6-button menu as the default).
- LINE OA Manager → Response settings: turn **off** the greeting message (the bot sends its own welcome) and
  check whether manual chat can stay on alongside the webhook, so non-command messages reach you.
- OA profile / LIFF app: privacy policy URL `https://<domain>/privacy`.
- LIFF: see README "Citizen water reports". Endpoint `https://<domain>/report`.
- Reply links use `PUBLIC_BASE_URL`, which setup.sh writes into `/etc/floodwatch.env` from the deploy domain.
- Project mailbox: `project.contact_email` in `config/settings.yaml` (namfonfoon.th@gmail.com), shown on `/privacy`.

## Operate
- Logs: `journalctl -u floodwatch -f` (no userIds or coordinates are logged).
- Backups: daily 03:15 server time to `/var/lib/floodwatch/backup/`, 14 days kept, on the same disk. Copy off-box if it matters.
- Single process only: do not raise `--workers`.
