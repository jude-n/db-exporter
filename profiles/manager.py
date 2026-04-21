"""
Profile manager — JSON files under ~/.db_exporter/profiles/.
Passwords are stored in the OS keychain via keyring_store.py.

Profile JSON structure:
  connection       — dialect/host/port/user/database (no password)
  selected_tables  — list of table names
  output_folder    — export destination (may be auto-derived from group)
  format           — "csv" or "sql"
  group_id         — optional UUID linking to a group in groups.json
"""
import json
import os
from typing import List, Optional


DEFAULT_DIR = os.path.join(os.path.expanduser("~"), ".db_exporter", "profiles")


class ProfileManager:
    def __init__(self, base_dir: str = DEFAULT_DIR):
        self.base_dir = base_dir
        os.makedirs(self.base_dir, exist_ok=True)

    def _safe_name(self, name: str) -> str:
        safe = "".join(c for c in name if c.isalnum() or c in ("-", "_", " ", ".")).strip()
        if not safe:
            raise ValueError("Invalid profile name.")
        return safe

    def _path(self, name: str) -> str:
        return os.path.join(self.base_dir, f"{self._safe_name(name)}.json")

    def list(self) -> List[str]:
        if not os.path.isdir(self.base_dir):
            return []
        return sorted(
            os.path.splitext(fn)[0]
            for fn in os.listdir(self.base_dir)
            if fn.endswith(".json")
        )

    def list_with_meta(self) -> List[dict]:
        """Return list of {name, group_id, format, output_folder} for all profiles."""
        result = []
        for name in self.list():
            data = self.load(name) or {}
            result.append({
                "name": name,
                "group_id": data.get("group_id"),
                "format": data.get("format", "csv"),
                "output_folder": data.get("output_folder", ""),
            })
        return result

    def save(self, name: str, data: dict) -> None:
        with open(self._path(name), "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)

    def load(self, name: str) -> Optional[dict]:
        path = self._path(name)
        if not os.path.exists(path):
            return None
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)

    def rename(self, old_name: str, new_name: str) -> None:
        """Rename a profile by renaming its JSON file. Preserves all data."""
        old_path = self._path(old_name)
        new_path = self._path(new_name)
        if not os.path.exists(old_path):
            raise FileNotFoundError(f"Profile '{old_name}' not found.")
        if os.path.exists(new_path):
            raise FileExistsError(f"Profile '{new_name}' already exists.")
        os.rename(old_path, new_path)

    def delete(self, name: str) -> None:
        path = self._path(name)
        if os.path.exists(path):
            os.remove(path)

    def ungroup(self, group_id: str) -> List[str]:
        """Remove group_id from all profiles in a group. Returns affected profile names."""
        affected = []
        for name in self.list():
            data = self.load(name)
            if data and data.get("group_id") == group_id:
                data["group_id"] = None
                self.save(name, data)
                affected.append(name)
        return affected

    def reassign_group(self, profile_name: str, new_group_id: Optional[str]) -> None:
        """Move a profile to a different group (or ungroup if new_group_id is None)."""
        data = self.load(profile_name)
        if data is None:
            raise FileNotFoundError(f"Profile '{profile_name}' not found.")
        data["group_id"] = new_group_id
        self.save(profile_name, data)
