# -*- coding: utf-8 -*-
"""
WindowsBatchScriptManager - Web Dashboard

提供独立运行的 Web Dashboard 服务，支持实时状态推送（WebSocket）和 RESTful API。

依赖：pip install fastapi "uvicorn[standard]"

用法：
    python web_dashboard.py --port 8766 --api-key my-secret
    python web_dashboard.py --port 8766 --host 0.0.0.0 --api-key my-secret
    默认绑定 127.0.0.1；未提供 --api-key 时使用环境变量 API_KEY（默认 change-me-in-production）。
"""

import argparse
import asyncio
import json
import os
import sys
import threading
import time

# 确保能导入主模块
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from pathlib import Path

try:
    from fastapi import Depends, FastAPI, WebSocket, WebSocketDisconnect, Header, HTTPException, Query
    from fastapi.responses import HTMLResponse, JSONResponse
    import uvicorn
    HAS_FASTAPI = True
except ImportError:
    HAS_FASTAPI = False

# 导入主模块的工具函数
from main import (
    load_config, save_config, get_script_type, is_process_running,
    get_process_tree_pids, get_ports_for_pids, start_script, kill_process,
    CONFIG_PATH, HAS_PSUTIL, fire_plugin_hook,
)


# ============ SPA 前端 ============

DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>Batch Script Manager - Web Dashboard</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
:root{--bg:#0f172a;--card:#1e293b;--border:#334155;--text:#e2e8f0;--muted:#94a3b8;--accent:#60a5fa;--green:#6ee7b7;--red:#fca5a5;--yellow:#fcd34d}
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;background:var(--bg);color:var(--text);min-height:100vh}
.header{background:var(--card);padding:16px 24px;border-bottom:1px solid var(--border);display:flex;align-items:center;justify-content:space-between;position:sticky;top:0;z-index:10}
.header h1{font-size:20px;color:var(--accent)}
.header .info{font-size:13px;color:var(--muted)}
.container{padding:20px;max-width:1400px;margin:0 auto}
.toolbar{display:flex;gap:8px;margin-bottom:16px;flex-wrap:wrap}
.btn{padding:8px 16px;border-radius:8px;border:none;font-size:13px;cursor:pointer;font-weight:600;transition:all .15s}
.btn:hover{opacity:.85;transform:translateY(-1px)}
.btn-start{background:#059669;color:#fff}.btn-stop{background:#dc2626;color:#fff}
.btn-restart{background:#2563eb;color:#fff}.btn-refresh{background:#475569;color:#fff}
.stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:12px;margin-bottom:20px}
.stat-card{background:var(--card);border:1px solid var(--border);border-radius:12px;padding:16px;text-align:center}
.stat-card .value{font-size:28px;font-weight:700;color:var(--accent)}
.stat-card .label{font-size:12px;color:var(--muted);margin-top:4px}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(380px,1fr));gap:12px}
.card{background:var(--card);border-radius:12px;border:1px solid var(--border);padding:16px;transition:border-color .2s}
.card:hover{border-color:var(--accent)}
.card-header{display:flex;justify-content:space-between;align-items:center;margin-bottom:10px}
.card-header h3{font-size:15px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:60%}
.badge{padding:3px 10px;border-radius:99px;font-size:11px;font-weight:700;letter-spacing:.5px}
.badge-running{background:#065f46;color:var(--green)}.badge-stopped{background:#7f1d1d;color:var(--red)}
.badge-waiting{background:#78350f;color:var(--yellow)}
.card-info{font-size:12px;color:var(--muted);margin-bottom:10px;line-height:1.6}
.card-info span{display:inline-block;margin-right:12px}
.actions{display:flex;gap:4px;flex-wrap:wrap}
.ws-status{width:8px;height:8px;border-radius:50%;display:inline-block;margin-right:6px}
.ws-connected{background:#6ee7b7}.ws-disconnected{background:#fca5a5}
@media(max-width:768px){.grid{grid-template-columns:1fr}.header{flex-direction:column;gap:8px}}
</style>
</head>
<body>
<div class="header">
  <h1>Batch Script Manager</h1>
  <div class="info"><span class="ws-status ws-disconnected" id="ws-dot"></span><span id="clock"></span></div>
</div>
<div class="container">
  <div class="stats" id="stats"></div>
  <div class="toolbar">
    <button class="btn btn-start" onclick="api('start-all')">全部启动</button>
    <button class="btn btn-stop" onclick="api('stop-all')">全部停止</button>
    <button class="btn btn-restart" onclick="api('restart-all')">全部重启</button>
    <button class="btn btn-refresh" onclick="loadData()">刷新</button>
  </div>
  <div class="grid" id="scripts"></div>
</div>
<script>
const API=location.origin;
const API_KEY=(new URLSearchParams(location.search).get('key')||localStorage.getItem('bsm_api_key')||'');
if(API_KEY)localStorage.setItem('bsm_api_key',API_KEY);
function authHeaders(){return API_KEY?{'X-API-Key':API_KEY}:{}};
let ws=null;
function connectWS(){
  const proto=location.protocol==='https:'?'wss:':'ws:';
  const q=API_KEY?('?key='+encodeURIComponent(API_KEY)):'';
  ws=new WebSocket(proto+'//'+location.host+'/ws'+q);
  ws.onopen=()=>{document.getElementById('ws-dot').className='ws-status ws-connected'};
  ws.onclose=()=>{document.getElementById('ws-dot').className='ws-status ws-disconnected';setTimeout(connectWS,3000)};
  ws.onmessage=(e)=>{try{const d=JSON.parse(e.data);renderData(d)}catch(err){}};
}
async function api(action,id){
  let url=API+'/'+action;
  if(id)url+='?id='+id;
  await fetch(url,{method:'POST',headers:authHeaders()});
  setTimeout(loadData,500);
}
async function loadData(){
  try{const r=await fetch(API+'/api/list',{headers:authHeaders()});const d=await r.json();renderData(d)}catch(e){console.error(e)}
}
function renderData(d){
  const items=d.items||[];
  const running=items.filter(s=>s.running).length;
  const stopped=items.length-running;
  document.getElementById('stats').innerHTML=`
    <div class="stat-card"><div class="value">${items.length}</div><div class="label">Total</div></div>
    <div class="stat-card"><div class="value" style="color:var(--green)">${running}</div><div class="label">Running</div></div>
    <div class="stat-card"><div class="value" style="color:var(--red)">${stopped}</div><div class="label">Stopped</div></div>`;
  document.getElementById('scripts').innerHTML=items.map(s=>`
    <div class="card">
      <div class="card-header">
        <h3>${esc(s.name)}</h3>
        <span class="badge badge-${s.running?'running':'stopped'}">${s.running?'Running':'Stopped'}</span>
      </div>
      <div class="card-info">
        <span>ID: ${s.id}</span>
        <span>PID: ${s.pid||'-'}</span>
        <span>Port: ${s.ports||'-'}</span>
        <span>Group: ${esc(s.group||'-')}</span>
      </div>
      <div class="actions">
        <button class="btn btn-start" onclick="api('start',${s.id})">Start</button>
        <button class="btn btn-stop" onclick="api('stop',${s.id})">Stop</button>
        <button class="btn btn-restart" onclick="api('restart',${s.id})">Restart</button>
      </div>
    </div>`).join('');
}
function esc(s){return (s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;')}
setInterval(loadData,5000);loadData();connectWS();
setInterval(()=>document.getElementById('clock').textContent=new Date().toLocaleTimeString(),1000);
</script>
</body>
</html>"""


def start_web_dashboard(port: int, app_instance=None, host: str = "127.0.0.1", api_key: str = ""):
    """启动 Web Dashboard（供 main.py 调用或独立运行）。"""
    if not HAS_FASTAPI:
        raise ImportError("Web Dashboard requires fastapi and uvicorn: pip install fastapi 'uvicorn[standard]'")

    web_app = FastAPI(title="Batch Script Manager Dashboard")
    _app = app_instance
    _api_key = (api_key or os.environ.get("API_KEY") or "change-me-in-production").strip()

    def _check_key(x_api_key: str = Header(default="", alias="X-API-Key"), key: str = Query(default="")):
        provided = (x_api_key or key or "").strip()
        if not _api_key:
            return
        if provided != _api_key:
            raise HTTPException(status_code=401, detail="Invalid or missing X-API-Key")

    def _get_items():
        raw = load_config()
        items = []
        for r in raw:
            path = r.get("path", "")
            stype = r.get("script_type") or (get_script_type(path) or "batch")
            interpreter = r.get("interpreter") or r.get("python_exe") or ""
            items.append({
                "id": r.get("id", 0), "name": r.get("name", ""), "path": path,
                "group": r.get("group", ""), "script_type": stype, "interpreter": interpreter,
                "auto_restart": r.get("auto_restart", False), "log_output": r.get("log_output", False),
                "schedule_enabled": r.get("schedule_enabled", False),
                "depends_on": r.get("depends_on", ""), "args": r.get("args", ""),
            })
        return items

    def _find_item(items, id_or_name):
        id_or_name = (id_or_name or "").strip()
        try:
            sid = int(id_or_name)
            for it in items:
                if it.get("id") == sid:
                    return it
        except ValueError:
            pass
        nl = id_or_name.lower()
        for it in items:
            if nl in (it.get("name") or "").lower():
                return it
        return None

    @web_app.get("/", response_class=HTMLResponse)
    async def index():
        return DASHBOARD_HTML

    @web_app.get("/api/list")
    async def api_list(_: None = Depends(_check_key)):
        items = _get_items()
        # 如果有 app_instance，从内存读取运行状态
        if _app:
            id_map = {it.get("id"): it for it in _app.items}
            for r in items:
                live = id_map.get(r["id"])
                if live:
                    pid = live.get("pid")
                    running = pid and is_process_running(pid)
                    r["running"] = running
                    r["pid"] = pid if running else None
                    if running and pid:
                        pids = get_process_tree_pids(pid)
                        ports = get_ports_for_pids(pids)
                        r["ports"] = ",".join(map(str, ports)) if ports else "-"
                    else:
                        r["ports"] = "-"
                else:
                    r["running"] = False
                    r["pid"] = None
                    r["ports"] = "-"
        return {"items": items}

    @web_app.websocket("/ws")
    async def websocket_endpoint(websocket: WebSocket, key: str = Query(default="")):
        if _api_key and (key or "").strip() != _api_key:
            await websocket.close(code=4401)
            return
        await websocket.accept()
        try:
            while True:
                data = await _get_ws_data()
                await websocket.send_json(data)
                await asyncio.sleep(3)
        except WebSocketDisconnect:
            pass
        except Exception:
            pass

    async def _get_ws_data():
        items = _get_items()
        if _app:
            id_map = {it.get("id"): it for it in _app.items}
            for r in items:
                live = id_map.get(r["id"])
                if live:
                    pid = live.get("pid")
                    r["running"] = pid and is_process_running(pid)
                    r["pid"] = pid if r["running"] else None
                else:
                    r["running"] = False
                    r["pid"] = None
        return {"items": items}

    @web_app.post("/start")
    async def api_start(id: str = Query(default=""), _: None = Depends(_check_key)):
        if _app:
            items = _app.items
        else:
            items = _get_items()
        it = _find_item(items, id)
        if not it:
            return {"ok": False, "error": "Not found"}
        if _app:
            _app.root.after(0, lambda: _app._start_single_script(it))
        return {"ok": True}

    @web_app.post("/stop")
    async def api_stop(id: str = Query(default=""), _: None = Depends(_check_key)):
        items = _get_items() if not _app else _app.items
        it = _find_item(items, id)
        if not it:
            return {"ok": False, "error": "Not found"}
        pid = it.get("pid")
        if pid:
            for px in get_process_tree_pids(pid):
                kill_process(px)
            it["pid"] = None
        return {"ok": True}

    @web_app.post("/restart")
    async def api_restart(id: str = Query(default=""), _: None = Depends(_check_key)):
        items = _get_items() if not _app else _app.items
        it = _find_item(items, id)
        if not it:
            return {"ok": False, "error": "Not found"}
        pid = it.get("pid")
        if pid:
            for px in get_process_tree_pids(pid):
                kill_process(px)
            it["pid"] = None
        if _app:
            _app.root.after(0, lambda: _app._start_single_script(it))
        return {"ok": True}

    @web_app.post("/start-all")
    async def api_start_all(_: None = Depends(_check_key)):
        if _app:
            _app.root.after(0, _app._on_start_all)
        return {"ok": True}

    @web_app.post("/stop-all")
    async def api_stop_all(_: None = Depends(_check_key)):
        if _app:
            _app.root.after(0, _app._on_stop_all)
        return {"ok": True}

    @web_app.post("/restart-all")
    async def api_restart_all(_: None = Depends(_check_key)):
        if _app:
            _app.root.after(0, _app._on_restart_all)
        return {"ok": True}

    import asyncio
    threading.Thread(target=lambda: uvicorn.run(web_app, host=host, port=port, log_level="warning"), daemon=True).start()


if __name__ == "__main__":
    if not HAS_FASTAPI:
        print("Error: fastapi and uvicorn are required.")
        print("Install: pip install fastapi 'uvicorn[standard]'")
        sys.exit(1)

    parser = argparse.ArgumentParser(description="Batch Script Manager - Web Dashboard")
    parser.add_argument("--port", type=int, default=8766)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--api-key", default="")
    args = parser.parse_args()

    import asyncio

    start_web_dashboard(args.port, host=args.host, api_key=args.api_key)
    key_hint = args.api_key or os.environ.get("API_KEY") or "change-me-in-production"
    print(f"Web Dashboard running at http://localhost:{args.port}/?key={key_hint}")
    print("Press Ctrl+C to stop")

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nStopped.")
