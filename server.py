"""
FastAPI backend for DB Exporter.
Runs on a background thread; PyWebView opens a native window pointed at it.
"""
from __future__ import annotations

import os
import re
import sys
import time
import threading
from typing import Any, Optional

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel

from db.factory import get_connector, SUPPORTED_DIALECTS
from exporters.csv_exporter import export_tables_to_csv
from exporters.sql_exporter import export_tables_to_sql
from profiles.manager import ProfileManager
from profiles.groups import GroupRegistry
from profiles.keyring_store import get_password, set_password, delete_password


# ---------- Resource path ----------

def _resource_path(relative: str) -> str:
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, relative)


# ---------- Security helpers ----------

_PROFILE_NAME_RE = re.compile(r'^[\w\s\-\.]{1,64}$')
_GROUP_NAME_RE   = re.compile(r'^[\w\s\-\.]{1,64}$')
_HEX_COLOR_RE    = re.compile(r'^#[0-9a-fA-F]{6}$')


def _validate_profile_name(name: str) -> str:
    if not name or not _PROFILE_NAME_RE.match(name):
        raise HTTPException(status_code=400,
            detail="Invalid profile name. Use letters, numbers, spaces, hyphens, underscores, or dots (max 64 chars).")
    return name.strip()


def _validate_group_name(name: str) -> str:
    if not name or not _GROUP_NAME_RE.match(name):
        raise HTTPException(status_code=400,
            detail="Invalid group name. Use letters, numbers, spaces, hyphens, underscores, or dots (max 64 chars).")
    return name.strip()


def _validate_hex_color(color: str) -> str:
    if not color or not _HEX_COLOR_RE.match(color):
        raise HTTPException(status_code=400, detail="Color must be a hex string like #3b82f6.")
    return color.lower()


def _validate_output_path(path: str) -> str:
    if not path or not path.strip():
        raise HTTPException(status_code=400, detail="Output folder cannot be empty.")
    resolved = os.path.realpath(os.path.expanduser(path.strip()))
    blocked_roots = ["/etc", "/usr", "/bin", "/sbin", "/var", "/sys", "/proc"]
    if sys.platform == "win32":
        windir = os.environ.get("WINDIR", "C:\\Windows")
        blocked_roots = [windir.lower(), os.path.join(windir, "System32").lower()]
    for blocked in blocked_roots:
        if resolved.lower().startswith(blocked.lower()):
            raise HTTPException(status_code=400, detail=f"Output folder '{resolved}' is not allowed.")
    return resolved


# ---------- App ----------

app = FastAPI(title="DB Exporter API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:5177", "http://localhost:5177"],
    allow_methods=["GET", "POST", "DELETE", "PATCH"],
    allow_headers=["Content-Type"],
)

profile_manager = ProfileManager()
group_registry  = GroupRegistry()

_connector      = None
_connector_lock = threading.Lock()

_progress: dict[str, Any] = {
    "current": 0, "total": 0, "table": "",
    "profile": "", "profile_current": 0, "profile_total": 0,
    "group": "", "group_color": "",
    "done": False, "error": None, "summary": [],
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
    original_name: Optional[str] = None   # set when editing existing profile
    connection: dict
    selected_tables: list[str]
    output_folder: str
    format: str = "csv"
    group_id: Optional[str] = None


class MoveProfileRequest(BaseModel):
    target_group_id: Optional[str] = None
    is_copy: bool = False


class BatchRunRequest(BaseModel):
    profiles: list[str]
    base_output_folder: str = ""


class CreateGroupRequest(BaseModel):
    name: str
    color: str = "#3b82f6"
    base_output_folder: str = ""


class UpdateGroupRequest(BaseModel):
    name: Optional[str] = None
    color: Optional[str] = None
    base_output_folder: Optional[str] = None


# ---------- Helpers ----------

def _resolve_profile_output(profile_name: str, saved_folder: str,
                             group_id: Optional[str]) -> str:
    """
    Return the effective output path for a profile.
    If the profile has a non-empty saved_folder, use it.
    Otherwise auto-derive from group.
    """
    if saved_folder and saved_folder.strip():
        return saved_folder.strip()
    if group_id:
        return group_registry.derive_output_path(group_id, profile_name)
    return os.path.join(os.path.expanduser("~"), "db_exports", profile_name)


def _group_color(group_id: Optional[str]) -> str:
    if not group_id:
        return ""
    g = group_registry.get(group_id)
    return g["color"] if g else ""


# ---------- Routes ----------

@app.get("/")
def index():
    return FileResponse(_resource_path(os.path.join("ui", "index.html")))


@app.get("/api/dialects")
def get_dialects():
    return {"dialects": SUPPORTED_DIALECTS}


# -- Connection --

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
                try: _connector.close()
                except: pass
            _connector = conn
        tables = _connector.list_tables()
        return {"ok": True, "tables": tables}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/api/tables")
def list_tables():
    with _connector_lock:
        if not _connector:
            raise HTTPException(status_code=400, detail="Not connected.")
        try:
            return {"tables": _connector.list_tables()}
        except Exception as e:
            raise HTTPException(status_code=400, detail=str(e))


# -- Export --

@app.post("/api/export")
def export(req: ExportRequest):
    global _progress
    with _connector_lock:
        if not _connector:
            raise HTTPException(status_code=400, detail="Not connected.")
    if not req.tables:
        raise HTTPException(status_code=400, detail="No tables selected.")

    safe_out = _validate_output_path(req.output_folder)
    _progress = {
        "current": 0, "total": len(req.tables), "table": "",
        "profile": "", "profile_current": 0, "profile_total": 0,
        "group": "", "group_color": "",
        "done": False, "error": None, "summary": [],
    }

    def progress_cb(table, i, n):
        _progress.update({"current": i, "total": n, "table": table})

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
    return {"ok": True}


@app.get("/api/progress")
def get_progress():
    return _progress


# -- Groups --

@app.get("/api/groups")
def list_groups():
    return {"groups": group_registry.list()}


@app.post("/api/groups")
def create_group(req: CreateGroupRequest):
    _validate_group_name(req.name)
    _validate_hex_color(req.color)
    if group_registry.get_by_name(req.name):
        raise HTTPException(status_code=400, detail=f"Group '{req.name}' already exists.")
    base = _validate_output_path(req.base_output_folder) if req.base_output_folder.strip() else ""
    group = group_registry.create(req.name, req.color, base)
    return {"ok": True, "group": group}


@app.patch("/api/groups/{group_id}")
def update_group(group_id: str, req: UpdateGroupRequest):
    if not group_registry.get(group_id):
        raise HTTPException(status_code=404, detail="Group not found.")
    kwargs: dict = {}
    if req.name is not None:
        kwargs["name"] = _validate_group_name(req.name)
    if req.color is not None:
        kwargs["color"] = _validate_hex_color(req.color)
    if req.base_output_folder is not None:
        kwargs["base_output_folder"] = (
            _validate_output_path(req.base_output_folder)
            if req.base_output_folder.strip() else ""
        )
    group = group_registry.update(group_id, **kwargs)
    return {"ok": True, "group": group}


@app.delete("/api/groups/{group_id}")
def delete_group(group_id: str):
    if not group_registry.get(group_id):
        raise HTTPException(status_code=404, detail="Group not found.")
    # Ungroup all profiles in this group
    affected = profile_manager.ungroup(group_id)
    group_registry.delete(group_id)
    return {"ok": True, "ungrouped_profiles": affected}


# -- Profiles --

@app.get("/api/profiles")
def list_profiles():
    return {"profiles": profile_manager.list_with_meta()}


@app.post("/api/profiles")
def save_profile(req: SaveProfileRequest):
    _validate_profile_name(req.name)
    conn = dict(req.connection)
    password = conn.pop("password", "")

    # Resolve output folder
    out = req.output_folder.strip()
    if not out and req.group_id:
        out = group_registry.derive_output_path(req.group_id, req.name)

    data = {
        "connection": conn,
        "selected_tables": req.selected_tables,
        "output_folder": out,
        "format": req.format,
        "group_id": req.group_id,
    }

    # If editing an existing profile (name changed), rename the file
    original = (req.original_name or "").strip()
    if original and original.lower() != req.name.lower():
        # Name actually changed (not just case) — do a full rename
        _validate_profile_name(original)
        try:
            profile_manager.rename(original, req.name)
            old_pw = get_password(original) or password
            delete_password(original)
            set_password(req.name, old_pw)
        except FileExistsError:
            raise HTTPException(status_code=400, detail=f"Profile '{req.name}' already exists.")
        except FileNotFoundError:
            pass  # original didn't exist yet — just save new
    elif original and original != req.name:
        # Case-only rename — delete old keyring entry, save will overwrite the file
        old_pw = get_password(original) or password
        delete_password(original)
        set_password(req.name, old_pw)
        # Delete old file if safe name differs
        try:
            profile_manager.delete(original)
        except Exception:
            pass

    set_password(req.name, password)
    profile_manager.save(req.name, data)
    return {"ok": True}


@app.get("/api/profiles/{name}")
def load_profile(name: str):
    _validate_profile_name(name)
    data = profile_manager.load(name)
    if not data:
        raise HTTPException(status_code=404, detail="Profile not found.")
    data["connection"]["password"] = get_password(name) or ""
    # Attach group info for UI
    group_id = data.get("group_id")
    data["group"] = group_registry.get(group_id) if group_id else None
    # Auto-derive output if empty
    if not data.get("output_folder") and group_id:
        data["output_folder"] = group_registry.derive_output_path(group_id, name)
    return data


@app.delete("/api/profiles/{name}")
def delete_profile(name: str):
    _validate_profile_name(name)
    profile_manager.delete(name)
    delete_password(name)
    return {"ok": True}


@app.post("/api/profiles/{name}/move")
def move_profile(name: str, req: MoveProfileRequest):
    """Move (or copy) a profile to a different group."""
    _validate_profile_name(name)
    data = profile_manager.load(name)
    if not data:
        raise HTTPException(status_code=404, detail="Profile not found.")

    if req.is_copy:
        # Find a non-colliding name
        new_name = f"{name} (copy)"
        counter = 2
        while profile_manager.load(new_name):
            new_name = f"{name} (copy {counter})"
            counter += 1
        data["group_id"] = req.target_group_id
        # Auto-derive output for new group
        if req.target_group_id:
            data["output_folder"] = group_registry.derive_output_path(req.target_group_id, new_name)
        pw = get_password(name) or ""
        set_password(new_name, pw)
        profile_manager.save(new_name, data)
        return {"ok": True, "new_name": new_name}
    else:
        data["group_id"] = req.target_group_id
        if req.target_group_id:
            data["output_folder"] = group_registry.derive_output_path(req.target_group_id, name)
        profile_manager.save(name, data)
        return {"ok": True}


# -- Single profile run --

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

    group_id = data.get("group_id")
    raw_out = _resolve_profile_output(name, data.get("output_folder", ""), group_id)
    out = _validate_output_path(raw_out)
    fmt = data.get("format", "csv")
    color = _group_color(group_id)
    group_name = (group_registry.get(group_id) or {}).get("name", "") if group_id else ""

    _progress = {
        "current": 0, "total": len(wanted), "table": "",
        "profile": name, "profile_current": 1, "profile_total": 1,
        "group": group_name, "group_color": color,
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
                    try: _connector.close()
                    except: pass
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
                "summary": [{"profile": name, "group": group_name, "group_color": color,
                             "status": "ok", "tables": len(to_export), "output": out}],
            })
        except Exception as e:
            _progress.update({
                "done": True, "error": str(e),
                "summary": [{"profile": name, "group": group_name, "group_color": color,
                             "status": "error", "message": str(e)}],
            })

    threading.Thread(target=worker, daemon=True).start()
    return {"ok": True}


# -- Batch run --

@app.post("/api/profiles/batch-run")
def batch_run(req: BatchRunRequest):
    global _progress
    if not req.profiles:
        raise HTTPException(status_code=400, detail="No profiles selected.")

    _progress = {
        "current": 0, "total": 0, "table": "",
        "profile": "", "profile_current": 0, "profile_total": len(req.profiles),
        "group": "", "group_color": "",
        "done": False, "error": None, "summary": [],
    }

    def worker():
        global _connector
        summary = []

        for idx, name in enumerate(req.profiles, start=1):
            data = profile_manager.load(name)
            group_id = data.get("group_id") if data else None
            color = _group_color(group_id)
            group_name = (group_registry.get(group_id) or {}).get("name", "") if group_id else ""

            _progress.update({
                "profile": name, "profile_current": idx,
                "group": group_name, "group_color": color,
                "current": 0, "table": "",
            })

            if not data:
                summary.append({"profile": name, "group": group_name, "group_color": color,
                                 "status": "error", "message": "Profile not found."})
                continue

            password = get_password(name) or ""
            cfg = dict(data["connection"])
            cfg["password"] = password
            wanted = set(data.get("selected_tables", []))
            fmt = data.get("format", "csv")

            if req.base_output_folder:
                raw_out = os.path.join(req.base_output_folder, group_name, name) if group_name else os.path.join(req.base_output_folder, name)
            else:
                raw_out = _resolve_profile_output(name, data.get("output_folder", ""), group_id)

            try:
                out = _validate_output_path(raw_out)
            except HTTPException as path_err:
                summary.append({"profile": name, "group": group_name, "group_color": color,
                                 "status": "error", "message": path_err.detail})
                continue

            if not wanted:
                summary.append({"profile": name, "group": group_name, "group_color": color,
                                 "status": "skipped", "message": "No tables saved in profile."})
                continue

            try:
                conn = get_connector(cfg["dialect"])(cfg)
                conn.connect()
                with _connector_lock:
                    if _connector:
                        try: _connector.close()
                        except: pass
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

                entry = {"profile": name, "group": group_name, "group_color": color,
                         "status": "ok", "tables": len(to_export), "output": out}
                if missing:
                    entry["warning"] = f"{len(missing)} table(s) no longer exist: {', '.join(sorted(missing))}"
                summary.append(entry)

            except Exception as e:
                summary.append({"profile": name, "group": group_name, "group_color": color,
                                 "status": "error", "message": str(e)})
                continue

        _progress.update({"done": True, "error": None, "summary": summary,
                           "profile": "", "group": "", "group_color": "", "table": ""})

    threading.Thread(target=worker, daemon=True).start()
    return {"ok": True}


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
