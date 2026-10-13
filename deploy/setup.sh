#!/usr/bin/env bash
# Install or update FloodWatch on Ubuntu 24.04 (e.g. Oracle Cloud Always Free VM.Standard.E2.1.Micro).
# Usage (on the VM): sudo bash setup.sh <release.tar.gz> <domain>
#   domain: your own, or <public-ip-with-dashes>.sslip.io (free, no DNS setup), e.g. 203-0-113-7.sslip.io
set -euo pipefail
TARBALL=${1:?release tarball}; DOMAIN=${2:?domain}
APP=/opt/floodwatch; DATA=/var/lib/floodwatch; HERE=$(cd "$(dirname "$0")" && pwd)

if ! command -v caddy >/dev/null; then
  apt-get update
  apt-get install -y python3-venv python3-pip sqlite3 debian-keyring debian-archive-keyring apt-transport-https curl gpg
  curl -1sLf https://dl.cloudsmith.io/public/caddy/stable/gpg.key | gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
  curl -1sLf https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt > /etc/apt/sources.list.d/caddy-stable.list
  apt-get update && apt-get install -y caddy
fi

id floodwatch >/dev/null 2>&1 || useradd --system --home "$APP" --shell /usr/sbin/nologin floodwatch
install -d -o floodwatch -g floodwatch -m 750 "$DATA" "$DATA/backup"

# Fresh code each release; the database lives in $DATA and is never touched here.
rm -rf "$APP.new" && mkdir -p "$APP.new" && tar -xzf "$TARBALL" -C "$APP.new"
python3 -m venv "$APP.new/.venv"
"$APP.new/.venv/bin/pip" install --quiet --upgrade pip
"$APP.new/.venv/bin/pip" install --quiet -r "$APP.new/requirements.txt"
chown -R root:floodwatch "$APP.new" && chmod -R g+rX,o-rwx "$APP.new"
rm -rf "$APP.old"; [ -d "$APP" ] && mv "$APP" "$APP.old"; mv "$APP.new" "$APP"

if [ ! -f /etc/floodwatch.env ]; then
  install -o root -g floodwatch -m 640 "$HERE/floodwatch.env.example" /etc/floodwatch.env
  echo ">>> Fill in /etc/floodwatch.env, then: sudo systemctl restart floodwatch"
fi

install -m 644 "$HERE/floodwatch.service" /etc/systemd/system/floodwatch.service
sed "s/__DOMAIN__/$DOMAIN/" "$HERE/Caddyfile" > /etc/caddy/Caddyfile
# Daily consistent SQLite backup, keep 14 days.
cat > /etc/cron.d/floodwatch-backup <<CRON
15 3 * * * floodwatch [ -f $DATA/floodwatch.db ] && sqlite3 $DATA/floodwatch.db ".backup $DATA/backup/fw-\$(date +\%F).db" && find $DATA/backup -name 'fw-*.db' -mtime +14 -delete
CRON

# Oracle's Ubuntu images ship iptables rules that REJECT everything except SSH.
if iptables -S INPUT | grep -q REJECT && ! iptables -S INPUT | grep -q -- "--dport 443"; then
  iptables -I INPUT 5 -p tcp -m state --state NEW --dport 80 -j ACCEPT
  iptables -I INPUT 5 -p tcp -m state --state NEW --dport 443 -j ACCEPT
  command -v netfilter-persistent >/dev/null && netfilter-persistent save || true
fi

systemctl daemon-reload
systemctl enable --now floodwatch
systemctl restart floodwatch caddy
sleep 3
curl -fsS http://127.0.0.1:8000/health && echo " <- app OK"
echo "Map: https://$DOMAIN/map   Webhook: https://$DOMAIN/webhook/line   LIFF endpoint: https://$DOMAIN/report"
