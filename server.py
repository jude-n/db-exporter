"""
FastAPI backend for DB Exporter.
Runs on a background thread; PyWebView opens a native window pointed at it.
"""
from __future__ import annotations

import os
import sys
import time
import threading
from typing import Any

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from db.factory import get_connector, SUPPORTED_DIALECTS
from exporters.csv_exporter import export_tables_to_csv
from exporters.sql_exporter import export_tables_to_sql
from profiles.manager import ProfileManager
from profiles.keyring_store import get_password, set_password, delete_password


def _resource_path(relative: str) -> str:
    """
    Resolve a path to a bundled resource.
    - In development: relative to this file.
    - When frozen by PyInstaller: relative to sys._MEIPASS (the temp bundle dir).
    """
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, relative)


app = FastAPI(title="DB Exporter API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

profile_manager = ProfileManager()

# Active connector (shared state — single-user desktop app)
_connector = None
_connector_lock = threading.Lock()

# Progress tracking
_progress: dict[str, Any] = {"current": 0, "total": 0, "table": "", "done": False, "error": None}


# ---------- Models ----------

class ConnectRequest(BaseModel):
    dialect: str
    host: str
    port: int
    user: str
    password: str
    database: str


class ExportRequest(BaseModel):
    tables: list[str]
    output_folder: str
    format: str = "csv"  # "csv" or "sql"


class SaveProfileRequest(BaseModel):
    name: str
    connection: dict
    selected_tables: list[str]
    output_folder: str
    format: str = "csv"


# ---------- Routes ----------

@app.get("/")
def index():
    return FileResponse(_resource_path(os.path.join("ui", "index.html")))


@app.get("/api/dialects")
def get_dialects():
    return {"dialects": SUPPORTED_DIALECTS}


@app.post("/api/test-connection")
def test_connection(req: ConnectRequest):
    try:
        cfg = req.model_dump()
        conn = get_connector(cfg["dialect"])(cfg)
        conn.connect()
        conn.close()
        return {"ok": True}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/connect")
def connect(req: ConnectRequest):
    global _connector
    try:
        cfg = req.model_dump()
        conn = get_connector(cfg["dialect"])(cfg)
        conn.connect()
        with _connector_lock:
            if _connector:
                try:
                    _connector.close()
                except Exception:
                    pass
            _connector = conn
        tables = _connector.list_tables()
        return {"ok": True, "tables": tables}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/api/tables")
def list_tables():
    with _connector_lock:
        if not _connector:
            raise HTTPException(status_code=400, detail="Not connected. Load tables first.")
        try:
            tables = _connector.list_tables()
            return {"tables": tables}
        except Exception as e:
            raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/export")
def export(req: ExportRequest):
    global _progress
    with _connector_lock:
        if not _connector:
            raise HTTPException(status_code=400, detail="Not connected.")

    if not req.tables:
        raise HTTPException(status_code=400, detail="No tables selected.")

    _progress = {"current": 0, "total": len(req.tables), "table": "", "done": False, "error": None}

    def progress_cb(table, i, n):
        _progress.update({"current": i, "total": n, "table": table, "done": False, "error": None})

    def worker():
        try:
            os.makedirs(req.output_folder, exist_ok=True)
            if req.format == "sql":
                export_tables_to_sql(
                    _connector,
                    req.tables,
                    req.output_folder,
                    progress_cb=progress_cb,
                )
            else:
                export_tables_to_csv(
                    _connector,
                    req.tables,
                    req.output_folder,
                    progress_cb=progress_cb,
                )
            _progress.update({"current": len(req.tables), "done": True, "error": None})
        except Exception as e:
            _progress.update({"done": True, "error": str(e)})

    threading.Thread(target=worker, daemon=True).start()
    return {"ok": True, "message": "Export started."}


@app.get("/api/progress")
def get_progress():
    return _progress


# ---------- Profile routes ----------

@app.get("/api/profiles")
def list_profiles():
    return {"profiles": profile_manager.list()}


@app.post("/api/profiles")
def save_profile(req: SaveProfileRequest):
    # Strip password from connection before saving to disk
    conn = dict(req.connection)
    password = conn.pop("password", "")
    set_password(req.name, password)
    data = {
        "connection": conn,
        "selected_tables": req.selected_tables,
        "output_folder": req.output_folder,
        "format": req.format,
    }
    profile_manager.save(req.name, data)
    return {"ok": True}


@app.get("/api/profiles/{name}")
def load_profile(name: str):
    data = profile_manager.load(name)
    if not data:
        raise HTTPException(status_code=404, detail="Profile not found.")
    # Re-attach password from keyring
    password = get_password(name) or ""
    data["connection"]["password"] = password
    return data


@app.delete("/api/profiles/{name}")
def delete_profile(name: str):
    profile_manager.delete(name)
    delete_password(name)
    return {"ok": True}


@app.post("/api/profiles/{name}/run")
def run_profile(name: str):
    global _connector, _progress
    data = profile_manager.load(name)
    if not data:
        raise HTTPException(status_code=404, detail="Profile not found.")

    password = get_password(name) or ""
    cfg = dict(data["connection"])
    cfg["password"] = password

    wanted = set(data.get("selected_tables", []))
    if not wanted:
        raise HTTPException(status_code=400, detail="Profile has no tables saved.")

    out = data.get("output_folder", os.path.expanduser("~/db_exports"))
    fmt = data.get("format", "csv")
    _progress = {"current": 0, "total": len(wanted), "table": "", "done": False, "error": None}

    def progress_cb(table, i, n):
        _progress.update({"current": i, "total": n, "table": table, "done": False, "error": None})

    def worker():
        global _connector
        try:
            conn = get_connector(cfg["dialect"])(cfg)
            conn.connect()
            with _connector_lock:
                if _connector:
                    try:
                        _connector.close()
                    except Exception:
                        pass
                _connector = conn

            all_tables = set(_connector.list_tables())
            to_export = sorted(wanted & all_tables)
            os.makedirs(out, exist_ok=True)

            if fmt == "sql":
                export_tables_to_sql(_connector, to_export, out, progress_cb=progress_cb)
            else:
                export_tables_to_csv(_connector, to_export, out, progress_cb=progress_cb)

            _progress.update({"current": len(to_export), "done": True, "error": None})
        except Exception as e:
            _progress.update({"done": True, "error": str(e)})

    threading.Thread(target=worker, daemon=True).start()
    return {"ok": True, "message": f"Running profile '{name}'."}


# ---------- Server startup ----------

def start(host: str = "127.0.0.1", port: int = 5177):
    """Start uvicorn in a daemon thread. Returns when the server is ready."""
    config = uvicorn.Config(app, host=host, port=port, log_level="error")
    server = uvicorn.Server(config)

    t = threading.Thread(target=server.run, daemon=True)
    t.start()

    # Poll until uvicorn signals it's accepting connections
    for _ in range(50):
        time.sleep(0.1)
        if server.started:
            break

    return f"http://{host}:{port}"
