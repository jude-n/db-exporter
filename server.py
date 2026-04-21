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
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, relative)

import re

# ---------- Security helpers ----------

_PROFILE_NAME_RE = re.compile(r'^[\w\s\-\.]{1,64}$')

def _validate_profile_name(name: str) -> str:
    """Raise 400 if profile name contains path traversal or unsafe characters."""
    if not name or not _PROFILE_NAME_RE.match(name):
        raise HTTPException(
            status_code=400,
            detail="Invalid profile name. Use letters, numbers, spaces, hyphens, underscores, or dots (max 64 chars)."
        )
    return name.strip()


def _validate_output_path(path: str) -> str:
    """Resolve and validate output path — blocks path traversal and system directories."""
    if not path or not path.strip():
        raise HTTPException(status_code=400, detail="Output folder cannot be empty.")

    resolved = os.path.realpath(os.path.expanduser(path.strip()))

    blocked_roots = ["/etc", "/usr", "/bin", "/sbin", "/var", "/sys", "/proc"]
    if sys.platform == "win32":
        windir = os.environ.get("WINDIR", "C:\\Windows")
        blocked_roots = [windir.lower(), os.path.join(windir, "System32").lower()]

    for blocked in blocked_roots:
        if resolved.lower().startswith(blocked.lower()):
            raise HTTPException(
                status_code=400,
                detail=f"Output folder '{resolved}' is not allowed."
            )

    return resolved



app = FastAPI(title="DB Exporter API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:5177", "http://localhost:5177"],
    allow_methods=["GET", "POST", "DELETE"],
    allow_headers=["Content-Type"],
)

profile_manager = ProfileManager()

_connector = None
_connector_lock = threading.Lock()

_progress: dict[str, Any] = {
    "current": 0,
    "total": 0,
    "table": "",
    "profile": "",
    "profile_current": 0,
    "profile_total": 0,
    "done": False,
    "error": None,
    "summary": [],
}


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
    format: str = "csv"


class SaveProfileRequest(BaseModel):
    name: str
    connection: dict
    selected_tables: list[str]
    output_folder: str
    format: str = "csv"


class BatchRunRequest(BaseModel):
    profiles: list[str]
    base_output_folder: str = ""


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

    _progress = {
        "current": 0, "total": len(req.tables), "table": "",
        "profile": "", "profile_current": 0, "profile_total": 0,
        "done": False, "error": None, "summary": [],
    }

    def progress_cb(table, i, n):
        _progress.update({"current": i, "total": n, "table": table})

    safe_out = _validate_output_path(req.output_folder)

    def worker():
        try:
            os.makedirs(safe_out, exist_ok=True)
            if req.format == "sql":
                export_tables_to_sql(_connector, req.tables, safe_out, progress_cb=progress_cb)
            else:
                export_tables_to_csv(_connector, req.tables, safe_out, progress_cb=progress_cb)
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
    _validate_profile_name(req.name)
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
    _validate_profile_name(name)
    data = profile_manager.load(name)
    if not data:
        raise HTTPException(status_code=404, detail="Profile not found.")
    password = get_password(name) or ""
    data["connection"]["password"] = password
    return data


@app.delete("/api/profiles/{name}")
def delete_profile(name: str):
    _validate_profile_name(name)
    profile_manager.delete(name)
    delete_password(name)
    return {"ok": True}


@app.post("/api/profiles/{name}/run")
def run_profile(name: str):
    _validate_profile_name(name)
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

    out = _validate_output_path(data.get("output_folder", os.path.expanduser("~/db_exports")))
    fmt = data.get("format", "csv")
    _progress = {
        "current": 0, "total": len(wanted), "table": "",
        "profile": name, "profile_current": 1, "profile_total": 1,
        "done": False, "error": None, "summary": [],
    }

    def progress_cb(table, i, n):
        _progress.update({"current": i, "total": n, "table": table})

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

            _progress.update({
                "current": len(to_export), "done": True, "error": None,
                "summary": [{"profile": name, "status": "ok", "tables": len(to_export), "output": out}],
            })
        except Exception as e:
            _progress.update({
                "done": True, "error": str(e),
                "summary": [{"profile": name, "status": "error", "message": str(e)}],
            })

    threading.Thread(target=worker, daemon=True).start()
    return {"ok": True, "message": f"Running profile '{name}'."}


@app.post("/api/profiles/batch-run")
def batch_run(req: BatchRunRequest):
    """
    Run multiple profiles in sequence.
    Skips profiles that fail — logs everything in summary.
    Output goes to each profile's own saved folder, or base_output_folder/<profile_name>
    if base_output_folder is provided.
    """
    global _progress

    if not req.profiles:
        raise HTTPException(status_code=400, detail="No profiles selected.")

    _progress = {
        "current": 0, "total": 0, "table": "",
        "profile": "", "profile_current": 0, "profile_total": len(req.profiles),
        "done": False, "error": None, "summary": [],
    }

    def worker():
        global _connector
        summary = []

        for idx, name in enumerate(req.profiles, start=1):
            _progress.update({
                "profile": name,
                "profile_current": idx,
                "current": 0,
                "table": "",
            })

            data = profile_manager.load(name)
            if not data:
                summary.append({"profile": name, "status": "error", "message": "Profile not found."})
                continue

            password = get_password(name) or ""
            cfg = dict(data["connection"])
            cfg["password"] = password
            wanted = set(data.get("selected_tables", []))
            fmt = data.get("format", "csv")

            if req.base_output_folder:
                raw_out = os.path.join(req.base_output_folder, name)
            else:
                raw_out = data.get("output_folder", os.path.join(os.path.expanduser("~"), "db_exports", name))
            try:
                out = _validate_output_path(raw_out)
            except HTTPException as path_err:
                summary.append({"profile": name, "status": "error", "message": path_err.detail})
                continue

            if not wanted:
                summary.append({"profile": name, "status": "skipped", "message": "No tables saved in profile."})
                continue

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
                missing = wanted - all_tables

                _progress.update({"total": len(to_export)})

                def progress_cb(table, i, n):
                    _progress.update({"current": i, "total": n, "table": table})

                os.makedirs(out, exist_ok=True)

                if fmt == "sql":
                    export_tables_to_sql(_connector, to_export, out, progress_cb=progress_cb)
                else:
                    export_tables_to_csv(_connector, to_export, out, progress_cb=progress_cb)

                entry = {"profile": name, "status": "ok", "tables": len(to_export), "output": out}
                if missing:
                    entry["warning"] = f"{len(missing)} table(s) no longer exist: {', '.join(sorted(missing))}"
                summary.append(entry)

            except Exception as e:
                summary.append({"profile": name, "status": "error", "message": str(e)})
                continue

        _progress.update({"done": True, "error": None, "summary": summary, "profile": "", "table": ""})

    threading.Thread(target=worker, daemon=True).start()
    return {"ok": True, "message": f"Batch export started for {len(req.profiles)} profiles."}


# ---------- Server startup ----------

def start(host: str = "127.0.0.1", port: int = 5177):
    config = uvicorn.Config(app, host=host, port=port, log_level="error")
    server = uvicorn.Server(config)
    t = threading.Thread(target=server.run, daemon=True)
    t.start()
    for _ in range(50):
        time.sleep(0.1)
        if server.started:
            break
    return f"http://{host}:{port}"
