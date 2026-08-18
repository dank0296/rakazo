#!/usr/bin/env python3
"""Dizzbot owner — accounts only. Do not run against billing.kerogroup.ai."""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import subprocess
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

HOST = os.environ.get("OWNER_BIND", "0.0.0.0")
PORT = int(os.environ.get("OWNER_PORT", "8788"))
ENV_FILE = Path(os.environ.get("OWNER_ENV", "/opt/dizzbot-owner.env"))
RAKAZO_ENV = Path(os.environ.get("RAKAZO_ENV", "/opt/rakazo/.env"))
COOKIE = "dizzbot_owner"


def load_password() -> str:
    if os.environ.get("OWNER_PASSWORD"):
        return os.environ["OWNER_PASSWORD"]
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text().splitlines():
            if line.startswith("OWNER_PASSWORD="):
                return line.split("=", 1)[1].strip()
    return ""


PASSWORD = load_password()


def load_database_url() -> str:
    for line in RAKAZO_ENV.read_text().splitlines():
        if line.startswith("DATABASE_URL="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    raise SystemExit("DATABASE_URL missing in RAKAZO_ENV")


def sql(query: str, *args: str) -> list[list[str]]:
    if args:
        query = query.format(*[a.replace("'", "''") for a in args])
    cmd = [
        "docker",
        "compose",
        "--env-file",
        "/opt/rakazo/.env",
        "-f",
        "/opt/rakazo/infra/compose/docker-compose.yml",
        "exec",
        "-T",
        "postgres",
        "psql",
        "-U",
        "rakazo",
        "-d",
        "rakazo",
        "-At",
        "-F",
        "\t",
        "-v",
        "ON_ERROR_STOP=1",
        "-c",
        query,
    ]
    try:
        out = subprocess.check_output(cmd, text=True, stderr=subprocess.STDOUT, cwd="/opt/rakazo")
    except FileNotFoundError:
        raise RuntimeError("docker compose not found") from None
    except subprocess.CalledProcessError as exc:
        raise RuntimeError((exc.output or str(exc))[-500:]) from None
    rows = []
    for line in out.splitlines():
        if line.strip() and not line.startswith("WARNING"):
            rows.append(line.split("\t"))
    return rows


def token() -> str:
    if not PASSWORD:
        raise SystemExit("OWNER_PASSWORD is required")
    return hmac.new(b"dizzbot-owner", PASSWORD.encode(), hashlib.sha256).hexdigest()


HTML = """<!doctype html>
<meta charset="utf-8">
<title>Dizzbot owner</title>
<style>
  body{font:16px/1.4 system-ui,sans-serif;background:#0a0a14;color:#eee;margin:24px;max-width:880px}
  a,button,input{font:inherit}
  input,button{padding:8px 10px;border-radius:8px;border:1px solid #333;background:#161622;color:#fff}
  button{background:#a855f7;border:0;cursor:pointer}
  table{width:100%;border-collapse:collapse;margin:16px 0}
  th,td{text-align:left;padding:8px;border-bottom:1px solid #2a2a3a}
  .muted{color:#888}
  .row{display:flex;gap:8px;flex-wrap:wrap;margin:12px 0}
  .pill{background:#1f1f2e;padding:4px 8px;border-radius:999px;margin:2px}
</style>
<h1>Dizzbot accounts</h1>
<form method="post" action="login" class="row" id="login" hidden>
  <input type="password" name="password" placeholder="Owner password" required>
  <button>Unlock</button>
</form>
<div id="app" hidden>
  <h2>Allowlist</h2>
  <p class="muted">Only these emails can register. Empty allowlist = anyone can sign up.</p>
  <div id="allow"></div>
  <form method="post" action="allow" class="row">
    <input name="email" type="email" placeholder="payer@email.com" required>
    <button>Allow email</button>
  </form>
  <h2>Accounts</h2>
  <table>
    <thead><tr><th>Email</th><th>Name</th><th>Created</th><th></th></tr></thead>
    <tbody id="users"></tbody>
  </table>
  <p class="muted">Lock = wipe sessions + scramble password. They cannot sign in. Does not delete their bots.</p>
</div>
<script>
async function boot(){
  const me = await fetch('api/me').then(r=>r.json());
  if(!me.ok){ login.hidden=false; return; }
  app.hidden=false;
  const d = await fetch('api/state').then(r=>r.json());
  allow.innerHTML = (d.allowlist.length? d.allowlist.map(e=>
    `<span class="pill">${e} <button form="x" onclick="drop('${e}')">x</button></span>`
  ).join(' ') : '<span class="muted">empty — public signup is open</span>');
  users.innerHTML = d.users.map(u=>`<tr>
    <td>${u.email}</td><td>${u.name||''}</td><td>${u.created}</td>
    <td><button onclick="lock('${u.id}')">Lock</button></td>
  </tr>`).join('') || '<tr><td colspan="4" class="muted">no accounts</td></tr>';
}
async function drop(email){
  await fetch('api/allow',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({op:'remove',email})});
  boot();
}
async function lock(id){
  if(!confirm('Lock this account? They cannot sign in.')) return;
  await fetch('api/lock',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({id})});
  boot();
}
boot();
</script>
"""


def allowlist() -> list[str]:
    rows = sql(
        'SELECT "signupAllowlist" FROM deployment_settings WHERE id = \'default\''
    )
    if not rows or not rows[0][0]:
        return []
    return [x.strip().lower() for x in rows[0][0].split(",") if x.strip()]


def set_allowlist(emails: list[str]) -> None:
    joined = ",".join(sorted(set(emails)))
    sql(
        'INSERT INTO deployment_settings (id, "signupAllowlist", "signupsEnabled", "createdAt", "updatedAt") '
        "VALUES ('default', '{0}', true, NOW(), NOW()) "
        'ON CONFLICT (id) DO UPDATE SET "signupAllowlist" = EXCLUDED."signupAllowlist", "updatedAt" = NOW()',
        joined,
    )


def users() -> list[dict]:
    rows = sql(
        'SELECT id, email, name, "createdAt"::text FROM "user" ORDER BY "createdAt" DESC'
    )
    return [{"id": r[0], "email": r[1], "name": r[2], "created": r[3]} for r in rows]


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        print(self.address_string(), fmt % args)

    def _authed(self) -> bool:
        cookie = self.headers.get("Cookie", "")
        return f"{COOKIE}={token()}" in cookie

    def _json(self, code: int, obj: dict):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _html(self, code: int, text: str, headers: list[tuple[str, str]] | None = None):
        body = text.encode()
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        for k, v in headers or []:
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _path(self) -> str:
        path = urlparse(self.path).path
        if path.startswith("/owner"):
            path = path[len("/owner") :] or "/"
        return path

    def do_GET(self):
        path = self._path()
        if path in ("/", "/owner", "/owner/"):
            return self._html(200, HTML)
        if path == "/api/me":
            return self._json(200, {"ok": self._authed()})
        if path == "/api/state":
            if not self._authed():
                return self._json(401, {"ok": False})
            return self._json(200, {"allowlist": allowlist(), "users": users()})
        self._json(404, {"error": "not found"})

    def do_POST(self):
        path = self._path()
        n = int(self.headers.get("Content-Length", "0") or 0)
        raw = self.rfile.read(n).decode() if n else ""
        if path == "/login":
            pw = (parse_qs(raw).get("password") or [""])[0]
            if hmac.compare_digest(pw, PASSWORD):
                return self._html(
                    302,
                    "ok",
                    [
                        ("Location", "/owner/"),
                        (
                            "Set-Cookie",
                            f"{COOKIE}={token()}; HttpOnly; Secure; SameSite=Lax; Path=/",
                        ),
                    ],
                )
            return self._html(401, "bad password")
        if not self._authed():
            return self._json(401, {"ok": False})
        data = json.loads(raw) if raw.startswith("{") else {k: v[0] for k, v in parse_qs(raw).items()}
        if path in ("/allow", "/api/allow"):
            try:
                email = str(data.get("email", "")).strip().lower()
                op = data.get("op", "add")
                cur = allowlist()
                if op == "remove":
                    cur = [e for e in cur if e != email]
                elif email:
                    cur.append(email)
                set_allowlist(cur)
            except Exception as exc:
                print("allow-error", exc)
                return self._html(500, f"allow failed: {exc}")
            if path == "/allow":
                return self._html(302, "ok", [("Location", "/owner/")])
            return self._json(200, {"ok": True, "allowlist": allowlist()})
        if path == "/api/lock":
            uid = str(data.get("id", ""))
            if not uid:
                return self._json(400, {"error": "id"})
            sql("DELETE FROM session WHERE \"userId\" = '{0}'", uid)
            sql(
                "UPDATE account SET password = 'locked-' || id WHERE \"userId\" = '{0}'",
                uid,
            )
            return self._json(200, {"ok": True})
        self._json(404, {"error": "not found"})


if __name__ == "__main__":
    if not PASSWORD:
        raise SystemExit("set OWNER_PASSWORD")
    print(f"dizzbot-owner http://{HOST}:{PORT}/  (accounts only)")
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()
