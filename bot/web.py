import threading
from datetime import datetime, UTC
from flask import Flask, jsonify, request, render_template

from .config import WEB_HOST, WEB_PORT
from .logging_utils import log

app = Flask(__name__, template_folder="webapp/templates")

state = {
    "universe": [],
    "positions": {},
    "signals": [],
    "last_candle_time": None,
    "sweep_stats": {"last_canceled": 0, "ts": None},
}

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

def start_web_server():
    log("Starting web server on", WEB_HOST, WEB_PORT)
    threading.Thread(target=lambda: app.run(host=WEB_HOST, port=WEB_PORT), daemon=True).start()

def update_state(**kwargs):
    state.update(kwargs)
    return state

