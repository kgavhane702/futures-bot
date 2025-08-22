import threading
from datetime import datetime, UTC
from flask import Flask, jsonify, request, render_template, Response
import os, subprocess, sys

from .config import WEB_HOST, WEB_PORT
from .logging_utils import log, recent_logs, subscribe_logs, unsubscribe_logs

app = Flask(__name__, template_folder="webapp/templates")

state = {
    "universe": [],
    "positions": {},
    "signals": [],
    "last_candle_time": None,
    "sweep_stats": {"last_canceled": 0, "ts": None},
}

# Simple control plane for runner to read
_control = {"restart": False}

def get_control_flags():
    return dict(_control)

def _set_control_flag(key: str, value):
    _control[key] = value

@app.get("/api/state")
def api_state():
    return jsonify({
        "universe": state.get("universe", []),
        "positions": state.get("positions", {}),
        "signals": state.get("signals", []),
        "last_candle_time": state.get("last_candle_time"),
        "sweep_stats": state.get("sweep_stats", {}),
        "server_time": datetime.now(UTC).isoformat(),
    })

@app.get("/")
def index():
    return render_template("dashboard.html")

@app.get("/dashboard")
def dashboard():
    return render_template("dashboard.html")

@app.get("/ui")
def ui():
    return render_template("dashboard.html")

@app.get("/admin")
def admin_page():
    # Only show credentials on the Admin page
    current = {
        "API_KEY": os.getenv("API_KEY", ""),
        "API_SECRET": os.getenv("API_SECRET", ""),
        "UNIVERSE_SIZE": os.getenv("UNIVERSE_SIZE", ""),
    }
    if current.get("API_KEY"): current["API_KEY"] = "***" + current["API_KEY"][-4:]
    if current.get("API_SECRET"): current["API_SECRET"] = "***" + current["API_SECRET"][-4:]
    return render_template("admin.html", env=current)

@app.get("/api/logs")
def api_logs_snapshot():
    try:
        return jsonify({"lines": recent_logs()})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.get("/api/logs/stream")
def api_logs_stream():
    def event_stream():
        q = subscribe_logs()
        # send recent buffer first
        for line in recent_logs():
            yield f"data: {line}\n\n"
        try:
            while True:
                line = q.get()
                yield f"data: {line}\n\n"
        finally:
            unsubscribe_logs(q)
    return Response(event_stream(), mimetype='text/event-stream')

@app.get("/api/config")
def api_config():
    try:
        from .config import get_config
        cfg = get_config()
        # mask sensitive fields
        if cfg.get("API_KEY"): cfg["API_KEY"] = "***" + cfg["API_KEY"][-4:]
        if cfg.get("API_SECRET"): cfg["API_SECRET"] = "***" + cfg["API_SECRET"][-4:]
        return jsonify(cfg)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.post("/api/env")
def api_env_update():
    # Restrict updates to selected keys only
    try:
        body = request.get_json(force=True) or {}
        allowed_keys = {"API_KEY", "API_SECRET", "UNIVERSE_SIZE"}
        allowed = {k: v for k, v in body.items() if k in allowed_keys and isinstance(v, str)}
        if not allowed:
            return jsonify({"ok": False, "error": "No allowed keys provided"}), 400
        path = os.path.join(os.getcwd(), ".env")
        lines = []
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                lines = f.read().splitlines()
        # Build dict of existing
        kv = {}
        for ln in lines:
            if "=" in ln and not ln.strip().startswith("#"):
                k, v = ln.split("=", 1)
                kv[k] = v
        # Apply allowed updates; skip blanks to retain current values
        for k, v in allowed.items():
            if v.strip() == "":
                continue
            # Ignore masked credential placeholders from UI
            if k in ("API_KEY", "API_SECRET") and v.strip().startswith("***"):
                continue
            kv[k] = v
        out = [f"{k}={kv[k]}" for k in kv]
        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(out) + "\n")
        return jsonify({"ok": True, "updated": list(allowed.keys())})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

@app.post("/api/restart")
def api_restart():
    try:
        _set_control_flag("restart", True)
        return jsonify({"ok": True, "message": "Restart requested"})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

@app.post("/api/git_pull")
def api_git_pull():
    try:
        proc = subprocess.run(["git", "pull"], capture_output=True, text=True, cwd=os.getcwd(), timeout=60)
        return jsonify({"ok": proc.returncode == 0, "code": proc.returncode, "stdout": proc.stdout, "stderr": proc.stderr})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

def start_web_server():
    log("Starting web server on", WEB_HOST, WEB_PORT)
    threading.Thread(target=lambda: app.run(host=WEB_HOST, port=WEB_PORT), daemon=True).start()

def update_state(**kwargs):
    state.update(kwargs)
    return state

