"""
Group registry — stores groups in ~/.db_exporter/groups.json

Each group has:
  id                 — stable UUID, never changes even on rename
  name               — display name, can be renamed
  color              — hex color string e.g. "#3b82f6"
  base_output_folder — optional base path; profiles auto-derive from this
"""
from __future__ import annotations

import json
import os
import uuid
from typing import Optional


DEFAULT_PATH = os.path.join(os.path.expanduser("~"), ".db_exporter", "groups.json")


class GroupRegistry:
    def __init__(self, path: str = DEFAULT_PATH):
        self.path = path
        os.makedirs(os.path.dirname(path), exist_ok=True)
        self._groups: dict[str, dict] = {}
        self._load()

    def _load(self) -> None:
        if os.path.exists(self.path):
            try:
                with open(self.path, "r", encoding="utf-8") as f:
                    raw = json.load(f)
                self._groups = {g["id"]: g for g in raw}
            except Exception:
                self._groups = {}

    def _save(self) -> None:
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(list(self._groups.values()), f, indent=2)

    def list(self) -> list[dict]:
        return sorted(self._groups.values(), key=lambda g: g["name"].lower())

    def get(self, group_id: str) -> Optional[dict]:
        return self._groups.get(group_id)

    def get_by_name(self, name: str) -> Optional[dict]:
        for g in self._groups.values():
            if g["name"].lower() == name.lower():
                return g
        return None

    def create(self, name: str, color: str = "#3b82f6", base_output_folder: str = "") -> dict:
        group = {
            "id": str(uuid.uuid4()),
            "name": name.strip(),
            "color": color,
            "base_output_folder": base_output_folder,
        }
        self._groups[group["id"]] = group
        self._save()
        return group

    def update(self, group_id: str, name: Optional[str] = None,
               color: Optional[str] = None,
               base_output_folder: Optional[str] = None) -> dict:
        if group_id not in self._groups:
            raise KeyError(f"Group not found.")
        g = self._groups[group_id]
        if name is not None:
            g["name"] = name.strip()
        if color is not None:
            g["color"] = color
        if base_output_folder is not None:
            g["base_output_folder"] = base_output_folder
        self._save()
        return g

    def delete(self, group_id: str) -> None:
        self._groups.pop(group_id, None)
        self._save()

    def derive_output_path(self, group_id: str, profile_name: str) -> str:
        g = self._groups.get(group_id)
        if not g:
            return os.path.join(os.path.expanduser("~"), "db_exports", profile_name)
        base = g.get("base_output_folder") or os.path.join(
            os.path.expanduser("~"), "db_exports", g["name"]
        )
        return os.path.join(base, profile_name)
