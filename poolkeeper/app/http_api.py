from __future__ import annotations

import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict
from urllib.parse import urlparse

from app.audit import dump_json, setup_logging
from app.config import Config
from app.main import run_once

log = setup_logging()
ROOT = Path(__file__).resolve().parent.parent

_run_lock = threading.Lock()
_last_run: Dict[str, Any] = {}


def _load_yaml_config() -> Dict[str, Any]:
    import yaml

    path = Path(os.environ.get("POOLKEEPER_CONFIG", ROOT / "config.yaml"))
    if not path.is_file():
        path = ROOT / "config.example.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return data if isinstance(data, dict) else {}


def _save_yaml_config(data: Dict[str, Any]) -> None:
    import yaml

    path = Path(os.environ.get("POOLKEEPER_CONFIG", ROOT / "config.yaml"))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump(data, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )


def _public_config(data: Dict[str, Any]) -> Dict[str, Any]:
    # never return secrets
    out = json.loads(json.dumps(data))
    if isinstance(out.get("grok2api"), dict):
        out["grok2api"].pop("admin_password", None)
    if isinstance(out.get("register8787"), dict):
        out["register8787"].pop("web_token", None)
    return out


class Handler(BaseHTTPRequestHandler):
    server_version = "g2a-poolkeeper/1.0"

    def log_message(self, fmt: str, *args: Any) -> None:
        log.info("http " + fmt, *args)

    def _cors(self) -> None:
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET,PUT,POST,OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization, X-Poolkeeper-Token")

    def _json(self, code: int, payload: Any) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self._cors()
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _html(self, code: int, html: str) -> None:
        body = html.encode("utf-8")
        self.send_response(code)
        self._cors()
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _auth_ok(self) -> bool:
        expected = (os.environ.get("POOLKEEPER_UI_TOKEN") or "").strip()
        if not expected:
            return True
        got = (self.headers.get("X-Poolkeeper-Token") or "").strip()
        return got == expected

    def do_OPTIONS(self) -> None:  # noqa: N802
        self.send_response(204)
        self._cors()
        self.end_headers()

    def do_GET(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if path in ("/", "/ui", "/index.html"):
            self._html(200, UI_HTML)
            return
        if not self._auth_ok():
            self._json(401, {"error": "unauthorized"})
            return
        if path == "/api/health":
            self._json(200, {"ok": True, "service": "g2a-poolkeeper"})
            return
        if path == "/api/config":
            self._json(200, {"data": _public_config(_load_yaml_config())})
            return
        if path == "/api/status":
            cfg = Config.load()
            self._json(
                200,
                {
                    "data": {
                        "dry_run": cfg.probe_dry_run,
                        "replenish_enabled": cfg.replenish_enabled,
                        "waterline": {
                            "low": cfg.waterline_low,
                            "target": cfg.waterline_target,
                            "emergency": cfg.waterline_emergency,
                        },
                        "register_base_url": cfg.register_base_url,
                        "register_public_url": cfg.register_public_url,
                        "g2a_base_url": cfg.grok2api_base_url,
                        "last_run": _last_run,
                        "running": _run_lock.locked(),
                    }
                },
            )
            return
        self._json(404, {"error": "not_found"})

    def do_PUT(self) -> None:  # noqa: N802
        if not self._auth_ok():
            self._json(401, {"error": "unauthorized"})
            return
        path = urlparse(self.path).path
        if path != "/api/config":
            self._json(404, {"error": "not_found"})
            return
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            body = json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError:
            self._json(400, {"error": "invalid_json"})
            return
        patch = body.get("config") if isinstance(body, dict) and "config" in body else body
        if not isinstance(patch, dict):
            self._json(400, {"error": "config must be object"})
            return
        current = _load_yaml_config()
        # deep merge shallow sections
        for key, value in patch.items():
            if isinstance(value, dict) and isinstance(current.get(key), dict):
                merged = dict(current[key])
                merged.update(value)
                current[key] = merged
            else:
                current[key] = value
        # strip secrets if empty placeholders
        _save_yaml_config(current)
        log.info("config updated via UI: %s", dump_json(_public_config(current)))
        self._json(200, {"data": _public_config(current)})

    def do_POST(self) -> None:  # noqa: N802
        if not self._auth_ok():
            self._json(401, {"error": "unauthorized"})
            return
        path = urlparse(self.path).path
        if path != "/api/run":
            self._json(404, {"error": "not_found"})
            return
        if not _run_lock.acquire(blocking=False):
            self._json(409, {"error": "already_running"})
            return

        def worker() -> None:
            global _last_run
            try:
                os.chdir(ROOT)
                # load dotenv if present
                env_path = ROOT / ".env"
                if env_path.is_file():
                    for line in env_path.read_text(encoding="utf-8").splitlines():
                        line = line.strip()
                        if not line or line.startswith("#") or "=" not in line:
                            continue
                        k, _, v = line.partition("=")
                        k, v = k.strip(), v.strip().strip('"').strip("'")
                        if k and k not in os.environ:
                            os.environ[k] = v
                cfg = Config.load()
                cfg.once = True
                stats = run_once(cfg)
                _last_run = stats
            except Exception as exc:  # noqa: BLE001
                _last_run = {"error": str(exc)}
                log.exception("manual run failed")
            finally:
                _run_lock.release()

        threading.Thread(target=worker, name="poolkeeper-manual-run", daemon=True).start()
        self._json(202, {"data": {"started": True}})


UI_HTML = """<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1"/>
  <title>补号设置 · Poolkeeper</title>
  <style>
    :root { color-scheme: light dark; --bg:#0b0f14; --card:#121821; --fg:#e8eef7; --muted:#93a0b4; --line:#243041; --accent:#5b8cff; --ok:#3ecf8e; --warn:#f0b429; }
    * { box-sizing: border-box; }
    body { margin:0; font:14px/1.5 ui-sans-serif,system-ui,sans-serif; background:var(--bg); color:var(--fg); }
    .wrap { max-width:920px; margin:0 auto; padding:24px 16px 48px; }
    h1 { font-size:20px; font-weight:600; margin:0 0 4px; }
    .sub { color:var(--muted); margin-bottom:20px; }
    .card { background:var(--card); border:1px solid var(--line); border-radius:12px; padding:16px; margin-bottom:14px; }
    .grid { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:12px; }
    label { display:block; font-size:12px; color:var(--muted); margin-bottom:4px; }
    input, select { width:100%; background:#0d131c; color:var(--fg); border:1px solid var(--line); border-radius:8px; padding:8px 10px; }
    .row { display:flex; gap:8px; flex-wrap:wrap; margin-top:12px; }
    button { border:0; border-radius:8px; padding:8px 14px; cursor:pointer; background:var(--accent); color:#fff; font-weight:600; }
    button.secondary { background:#1c2636; color:var(--fg); border:1px solid var(--line); }
    .pill { display:inline-block; padding:2px 8px; border-radius:999px; background:#1c2636; color:var(--muted); font-size:12px; }
    .ok { color:var(--ok); } .warn { color:var(--warn); }
    pre { white-space:pre-wrap; word-break:break-word; background:#0d131c; border-radius:8px; padding:10px; border:1px solid var(--line); max-height:280px; overflow:auto; }
    @media (max-width:720px){ .grid{grid-template-columns:1fr;} }
  </style>
</head>
<body>
  <div class="wrap">
    <h1>补号设置</h1>
    <p class="sub">Grok2API 池测活 / 清号 / 水位补号（8787）。密钥请写在 poolkeeper <code>.env</code>，此页只改策略参数。</p>
    <div class="card">
      <div class="row" style="margin:0 0 10px; align-items:center; justify-content:space-between;">
        <span class="pill" id="statusPill">加载中…</span>
        <div class="row" style="margin:0;">
          <button type="button" class="secondary" id="reloadBtn">刷新</button>
          <button type="button" class="secondary" id="runBtn">立即跑一轮</button>
          <button type="button" id="saveBtn">保存</button>
        </div>
      </div>
      <div class="grid">
        <div><label>干跑 dry_run</label><select id="dry_run"><option value="true">true（只观察）</option><option value="false">false（真实动作）</option></select></div>
        <div><label>启用补号 replenish.enabled</label><select id="replenish_enabled"><option value="true">true</option><option value="false">false</option></select></div>
        <div><label>低水位 low</label><input id="low" type="number" min="0"/></div>
        <div><label>目标水位 target</label><input id="target" type="number" min="0"/></div>
        <div><label>紧急水位 emergency</label><input id="emergency" type="number" min="0"/></div>
        <div><label>每轮最多测活数</label><input id="max_probe" type="number" min="1" max="500"/></div>
        <div><label>测活并发</label><input id="concurrency" type="number" min="1" max="16"/></div>
        <div><label>测活超时秒</label><input id="timeout" type="number" min="5" max="60"/></div>
        <div><label>清号模式</label><select id="cleanup_mode"><option value="report_only">report_only</option><option value="disable">disable</option><option value="delete">delete</option></select></div>
        <div><label>每轮最多清号</label><input id="max_clean" type="number" min="0" max="200"/></div>
        <div><label>每轮最多注册</label><input id="max_register" type="number" min="0" max="500"/></div>
        <div><label>调度间隔（分钟）</label><input id="interval" type="number" min="1" max="1440"/></div>
        <div><label>优先本地库存</label><select id="inventory_first"><option value="true">true</option><option value="false">false</option></select></div>
        <div><label>有注册任务时暂停</label><select id="pause_active"><option value="true">true</option><option value="false">false</option></select></div>
      </div>
    </div>
    <div class="card">
      <div style="margin-bottom:8px;color:var(--muted);">最近一轮</div>
      <pre id="lastRun">{}</pre>
    </div>
  </div>
<script>
const $ = (id) => document.getElementById(id);
async function api(path, opts={}) {
  const r = await fetch(path, {headers:{'Content-Type':'application/json',...(opts.headers||{})}, ...opts});
  const j = await r.json().catch(()=>({}));
  if (!r.ok) throw new Error(j.error || r.statusText);
  return j;
}
function setSel(id, v){ $(id).value = String(v); }
async function load() {
  const [cfgR, stR] = await Promise.all([api('/api/config'), api('/api/status')]);
  const c = cfgR.data || {};
  const probe = c.probe || {}, water = c.waterline || {}, rep = c.replenish || {}, clean = c.cleanup || {}, sch = c.scheduler || {};
  setSel('dry_run', probe.dry_run !== false);
  setSel('replenish_enabled', rep.enabled !== false);
  $('low').value = water.low ?? 100;
  $('target').value = water.target ?? 150;
  $('emergency').value = water.emergency ?? 30;
  $('max_probe').value = probe.max_accounts_per_round ?? 100;
  $('concurrency').value = probe.concurrency ?? 5;
  $('timeout').value = probe.timeout_seconds ?? 20;
  setSel('cleanup_mode', clean.mode || 'disable');
  $('max_clean').value = clean.max_actions_per_round ?? 20;
  $('max_register').value = rep.max_register_per_round ?? 100;
  $('interval').value = sch.interval_minutes ?? 30;
  setSel('inventory_first', rep.inventory_first !== false);
  setSel('pause_active', rep.pause_when_existing_job_active !== false);
  const st = stR.data || {};
  $('statusPill').textContent = st.running ? '运行中' : (st.dry_run ? '干跑模式' : '实动模式');
  $('statusPill').className = 'pill ' + (st.running ? 'warn' : 'ok');
  $('lastRun').textContent = JSON.stringify(st.last_run || {}, null, 2);
}
async function save() {
  const config = {
    probe: {
      dry_run: $('dry_run').value === 'true',
      max_accounts_per_round: Number($('max_probe').value),
      concurrency: Number($('concurrency').value),
      timeout_seconds: Number($('timeout').value),
    },
    cleanup: {
      mode: $('cleanup_mode').value,
      max_actions_per_round: Number($('max_clean').value),
    },
    waterline: {
      low: Number($('low').value),
      target: Number($('target').value),
      emergency: Number($('emergency').value),
    },
    replenish: {
      enabled: $('replenish_enabled').value === 'true',
      inventory_first: $('inventory_first').value === 'true',
      pause_when_existing_job_active: $('pause_active').value === 'true',
      max_register_per_round: Number($('max_register').value),
    },
    scheduler: { interval_minutes: Number($('interval').value) },
  };
  await api('/api/config', {method:'PUT', body: JSON.stringify({config})});
  await load();
  alert('已保存');
}
$('reloadBtn').onclick = () => load().catch(e => alert(e.message));
$('saveBtn').onclick = () => save().catch(e => alert(e.message));
$('runBtn').onclick = () => api('/api/run', {method:'POST'}).then(()=>{alert('已启动一轮'); return load();}).catch(e=>alert(e.message));
load().catch(e => { $('statusPill').textContent = '加载失败: '+e.message; });
</script>
</body>
</html>
"""


def serve(host: str = "0.0.0.0", port: int = 9109) -> None:
    httpd = ThreadingHTTPServer((host, port), Handler)
    log.info("poolkeeper UI/API on http://%s:%s", host, port)
    httpd.serve_forever()


if __name__ == "__main__":
    serve(
        host=os.environ.get("POOLKEEPER_UI_HOST", "0.0.0.0"),
        port=int(os.environ.get("POOLKEEPER_UI_PORT", "9109")),
    )
