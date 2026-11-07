#!/bin/sh
set -eu
cd "$(dirname "$0")/.."
getent passwd radar-demo >/dev/null || useradd --system --home-dir /var/lib/radar-demo --shell /usr/sbin/nologin radar-demo
install -d -o root -g root -m 0755 /opt/radar-demo
install -m 0644 ./*.py ./palette.csv /opt/radar-demo/
install -d -o root -g root -m 0700 /etc/radar-demo
python3 - <<'PY'
import os,secrets
p='/etc/radar-demo/token.env'
if not os.path.exists(p):
    fd=os.open(p,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    with os.fdopen(fd,'w') as f:f.write('VPS_TOKEN='+secrets.token_urlsafe(48)+'\n')
os.chmod(p,0o600)
PY
install -d -o radar-demo -g radar-demo -m 0750 /var/lib/radar-demo /var/lib/radar-demo/source /var/lib/radar-demo/native
install -m 0644 deploy/radar-demo.service /etc/systemd/system/radar-demo.service
systemctl daemon-reload
systemctl enable --now radar-demo.service
systemctl is-active radar-demo.service
