#!/usr/bin/env bash
# WHERE: VPS HOST root@srv1710004
# Public Dank Bot on https://dizzbot.com  (keep Docker, keep allowlist)
set -euo pipefail
DOMAIN=dizzbot.com
ORIGIN="https://${DOMAIN}"
ENV=/opt/rakazo/.env
NGINX_CTN=hermes-nginx

need_dns() {
  python3 - <<'PY'
import socket, sys
want = "2.25.140.203"
ok = True
for h in ("dizzbot.com", "www.dizzbot.com"):
    try:
        ips = sorted({a[4][0] for a in socket.getaddrinfo(h, None, socket.AF_INET)})
    except Exception as e:
        print(f"DNS_FAIL {h} {e}")
        sys.exit(2)
    print(f"DNS {h} -> {','.join(ips)}")
    if want not in ips:
        ok = False
sys.exit(0 if ok else 3)
PY
}

if ! need_dns; then
  echo "STOP: point Namecheap A @ and A www to 2.25.140.203 (delete URL redirect). Then re-run."
  exit 3
fi

python3 - <<'PY'
from pathlib import Path
p = Path("/opt/rakazo/.env")
text = p.read_text()
repl = {
    "WEB_ORIGIN": "https://dizzbot.com",
    "BETTER_AUTH_URL": "https://dizzbot.com",
    "API_URL": "https://dizzbot.com",
}
for k, v in repl.items():
    lines, hit = [], False
    for line in text.splitlines(True):
        if line.startswith(f"{k}="):
            nl = "\n" if line.endswith("\n") else ""
            lines.append(f"{k}={v}{nl}")
            hit = True
        else:
            lines.append(line)
    text = "".join(lines)
    if not hit:
        if text and not text.endswith("\n"):
            text += "\n"
        text += f"{k}={v}\n"
p.write_text(text)
print("env_ok")
PY

cat >/tmp/dizzbot-http.conf <<'NGX'
server {
    listen 80;
    server_name dizzbot.com www.dizzbot.com;
    location /.well-known/acme-challenge/ { root /var/www/certbot; }
    location / { return 301 https://dizzbot.com$request_uri; }
}
NGX

cat >/tmp/dizzbot-https.conf <<'NGX'
server {
    listen 80;
    server_name dizzbot.com www.dizzbot.com;
    location /.well-known/acme-challenge/ { root /var/www/certbot; }
    location / { return 301 https://dizzbot.com$request_uri; }
}
server {
    listen 443 ssl;
    server_name dizzbot.com www.dizzbot.com;
    ssl_certificate /etc/letsencrypt/live/dizzbot.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/dizzbot.com/privkey.pem;
    client_max_body_size 32m;
    location / {
        proxy_pass http://127.0.0.1:5173;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-Proto https;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_read_timeout 3600s;
    }
}
NGX

if docker ps --format '{{.Names}}' | grep -qx "$NGINX_CTN"; then
  mkdir -p /docker/certbot-www/.well-known/acme-challenge
  chmod -R 755 /docker/certbot-www
  docker cp /tmp/dizzbot-http.conf "$NGINX_CTN":/etc/nginx/conf.d/dizzbot.com.conf
  docker exec "$NGINX_CTN" nginx -t
  docker exec "$NGINX_CTN" nginx -s reload
  docker run --rm \
    -v certbot-data:/etc/letsencrypt \
    -v /docker/certbot-www:/var/www/certbot \
    certbot/certbot certonly --webroot -w /var/www/certbot \
    -d dizzbot.com -d www.dizzbot.com \
    --email dkero08@gmail.com --agree-tos --non-interactive --expand
  docker cp /tmp/dizzbot-https.conf "$NGINX_CTN":/etc/nginx/conf.d/dizzbot.com.conf
  docker exec "$NGINX_CTN" nginx -t
  docker exec "$NGINX_CTN" nginx -s reload
else
  apt-get update -y
  apt-get install -y nginx certbot python3-certbot-nginx
  cp /tmp/dizzbot-http.conf /etc/nginx/sites-available/dizzbot.com
  ln -sfn /etc/nginx/sites-available/dizzbot.com /etc/nginx/sites-enabled/dizzbot.com
  nginx -t && systemctl reload nginx
  certbot --nginx -d dizzbot.com -d www.dizzbot.com \
    --email dkero08@gmail.com --agree-tos --non-interactive --redirect
fi

echo "RESTART_PNPM: Ctrl+C the pnpm pane, then: unset COMPOSIO_API_KEY; cd /opt/rakazo && pnpm dev"
echo "SMOKE: curl -sS https://dizzbot.com/  and  https://dizzbot.com/app/cmswo86840001v6kml961gykb"
