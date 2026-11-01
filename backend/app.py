"""FastAPI backend: stores alerts, relays rover status/commands, serves the dashboard.

Run (from the project folder):
    python -m backend.manage create-user admin      # once
    python -m backend                               # http://<laptop-ip>:8080

Who talks to it:
    run.py (the detector)  -> /api/device/*   with header X-Device-Token (config backend.device_token)
    dashboard / phone app  -> /api/*          with a login token (JWT)
"""
import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import jwt
import yaml
from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel

ROOT = Path(__file__).resolve().parent.parent
HERE = Path(__file__).resolve().parent
CFG = yaml.safe_load((ROOT / "config.yaml").read_text())
ALERTS_DIR = (ROOT / CFG["alerts"]["dir"]).resolve()
DEVICE_TOKEN = CFG["backend"]["device_token"]
DB_PATH = Path(os.environ.get("ROVER_DB", HERE / "surveillance.db"))
SECRET_PATH = HERE / ".jwt_secret"
TOKEN_HOURS = 12
ALERT_TYPES = ("fight", "theft", "loitering", "restricted")
MODES = ("manual", "guard", "patrol")
DRIVES = ("forward", "back", "left", "right", "stop")

if not SECRET_PATH.exists():
    SECRET_PATH.write_text(secrets.token_hex(32))
JWT_SECRET = SECRET_PATH.read_text().strip()


# ============================== database ==============================
class DB:
    def __init__(self, path):
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.lock = threading.Lock()
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY, username TEXT UNIQUE NOT NULL, pw_hash TEXT NOT NULL, created TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS alerts (
                id INTEGER PRIMARY KEY, time TEXT NOT NULL, type TEXT NOT NULL, message TEXT NOT NULL,
                track_ids TEXT, snapshot TEXT, clip TEXT, label TEXT, received TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS telemetry (id INTEGER PRIMARY KEY, time TEXT NOT NULL, data TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS commands (
                id INTEGER PRIMARY KEY, time TEXT NOT NULL, data TEXT NOT NULL, username TEXT, delivered INTEGER DEFAULT 0);
            CREATE INDEX IF NOT EXISTS alerts_time ON alerts(time);
        """)

    def run(self, sql, args=()):
        with self.lock:
            cur = self.conn.execute(sql, args)
            self.conn.commit()
            return cur

    def all(self, sql, args=()):
        with self.lock:
            return [dict(r) for r in self.conn.execute(sql, args).fetchall()]

    def one(self, sql, args=()):
        rows = self.all(sql, args)
        return rows[0] if rows else None


db = DB(DB_PATH)


def now_iso():
    return datetime.now().isoformat(timespec="seconds")


def hash_password(password, salt=None):
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 200_000).hex()
    return f"{salt}${digest}"


def check_password(password, stored):
    salt, _ = stored.split("$", 1)
    return hmac.compare_digest(hash_password(password, salt), stored)


def create_user(username, password):
    db.run("INSERT INTO users (username, pw_hash, created) VALUES (?, ?, ?)",
           (username, hash_password(password), now_iso()))


# ============================== auth ==============================
def current_user(authorization: str = Header(default=""), token: str = Query(default="")):
    """Login token from the Authorization header, or ?token= (needed for <img>/<video> links)."""
    raw = authorization.removeprefix("Bearer ").strip() or token
    try:
        return jwt.decode(raw, JWT_SECRET, algorithms=["HS256"])["sub"]
    except jwt.PyJWTError:
        raise HTTPException(401, "Please log in")


def device(x_device_token: str = Header(default="")):
    if not hmac.compare_digest(x_device_token, DEVICE_TOKEN):
        raise HTTPException(401, "Bad device token")


# ============================== app ==============================
app = FastAPI(title="AI Surveillance Rover")
LIVE = {"status": None, "seen": 0.0}
TELEMETRY_EVERY_S = 10


class Login(BaseModel):
    username: str
    password: str


class AlertIn(BaseModel):
    time: str
    type: str
    message: str
    track_ids: list = []
    snapshot: str | None = None
    clip: str | None = None


class Label(BaseModel):
    label: str          # "real" | "false_alarm"


class Command(BaseModel):
    mode: str | None = None
    drive: str | None = None


@app.post("/api/login")
def login(body: Login):
    user = db.one("SELECT * FROM users WHERE username = ?", (body.username,))
    if not user or not check_password(body.password, user["pw_hash"]):
        raise HTTPException(401, "Wrong username or password")
    exp = datetime.now(timezone.utc) + timedelta(hours=TOKEN_HOURS)
    return {"token": jwt.encode({"sub": user["username"], "exp": exp}, JWT_SECRET, algorithm="HS256"),
            "username": user["username"]}


@app.get("/api/me")
def me(user=Depends(current_user)):
    return {"username": user}


# ---------------- alerts ----------------
@app.get("/api/alerts")
def list_alerts(limit: int = 50, type: str | None = None, after_id: int = 0, user=Depends(current_user)):
    sql, args = "SELECT * FROM alerts WHERE id > ?", [after_id]
    if type:
        sql += " AND type = ?"
        args.append(type)
    rows = db.all(sql + " ORDER BY id DESC LIMIT ?", (*args, min(limit, 500)))
    for r in rows:
        r["track_ids"] = json.loads(r["track_ids"] or "[]")
    return rows


@app.get("/api/alerts/stats")
def alert_stats(user=Depends(current_user)):
    rows = db.all("SELECT type, COUNT(*) AS n, SUM(label = 'real') AS real, SUM(label = 'false_alarm') AS false_alarm "
                  "FROM alerts GROUP BY type")
    return {r["type"]: {"total": r["n"], "real": r["real"] or 0, "false_alarm": r["false_alarm"] or 0} for r in rows}


@app.patch("/api/alerts/{alert_id}")
def label_alert(alert_id: int, body: Label, user=Depends(current_user)):
    if body.label not in ("real", "false_alarm", ""):
        raise HTTPException(400, "label must be 'real' or 'false_alarm'")
    if db.run("UPDATE alerts SET label = ? WHERE id = ?", (body.label or None, alert_id)).rowcount == 0:
        raise HTTPException(404, "No such alert")
    return {"ok": True}


@app.get("/media/{path:path}")
def media(path: str, user=Depends(current_user)):
    file = (ALERTS_DIR / path).resolve()
    if not file.is_relative_to(ALERTS_DIR) or not file.is_file():
        raise HTTPException(404, "Not found")
    return FileResponse(file)


# ---------------- rover status & commands ----------------
@app.get("/api/status")
def status(user=Depends(current_user)):
    online = time.time() - LIVE["seen"] < 5
    return {"detector_online": online, "last_seen": LIVE["seen"] or None, **((LIVE["status"] or {}) if online else {})}


@app.post("/api/command")
def command(body: Command, user=Depends(current_user)):
    if body.mode and body.mode not in MODES:
        raise HTTPException(400, f"mode must be one of {MODES}")
    if body.drive and body.drive not in DRIVES:
        raise HTTPException(400, f"drive must be one of {DRIVES}")
    if not body.mode and not body.drive:
        raise HTTPException(400, "send a mode or a drive command")
    db.run("INSERT INTO commands (time, data, username) VALUES (?, ?, ?)",
           (now_iso(), body.model_dump_json(exclude_none=True), user))
    return {"ok": True}


# ---------------- device (detector) side ----------------
@app.post("/api/device/alerts", dependencies=[Depends(device)])
def device_alert(body: AlertIn):
    cur = db.run("INSERT INTO alerts (time, type, message, track_ids, snapshot, clip, received) "
                 "VALUES (?, ?, ?, ?, ?, ?, ?)",
                 (body.time, body.type, body.message, json.dumps(body.track_ids), body.snapshot, body.clip, now_iso()))
    return {"id": cur.lastrowid}


@app.post("/api/device/status", dependencies=[Depends(device)])
def device_status(body: dict):
    LIVE["status"], LIVE["seen"] = body, time.time()
    last = db.one("SELECT time FROM telemetry ORDER BY id DESC LIMIT 1")
    if not last or (datetime.now() - datetime.fromisoformat(last["time"])).total_seconds() >= TELEMETRY_EVERY_S:
        db.run("INSERT INTO telemetry (time, data) VALUES (?, ?)", (now_iso(), json.dumps(body)))
    return {"ok": True}


@app.get("/api/device/commands", dependencies=[Depends(device)])
def device_commands():
    rows = db.all("SELECT id, data FROM commands WHERE delivered = 0 ORDER BY id")
    if rows:
        db.run(f"UPDATE commands SET delivered = 1 WHERE id IN ({','.join('?' * len(rows))})", [r["id"] for r in rows])
    return [json.loads(r["data"]) for r in rows]


# ---------------- dashboard ----------------
@app.get("/")
def dashboard():
    return FileResponse(HERE / "static" / "index.html")
