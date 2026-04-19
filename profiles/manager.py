"""
Profile manager — JSON files under ~/.db_exporter/profiles/.

SECURITY NOTE: passwords are stored in plain text for MVP simplicity.
Before going to production, switch this to keyring or an encrypted blob
(e.g. via the `keyring` library, or Fernet with a password-derived key).
"""
import json
import os
from typing import List, Optional


DEFAULT_DIR = os.path.join(os.path.expanduser("~"), ".db_exporter", "profiles")


class ProfileManager:
    def __init__(self, base_dir: str = DEFAULT_DIR):
        self.base_dir = base_dir
        os.makedirs(self.base_dir, exist_ok=True)

    def _path(self, name: str) -> str:
        safe = "".join(c for c in name if c.isalnum() or c in ("-", "_", " ")).strip()
        if not safe:
            raise ValueError("Invalid profile name.")
        return os.path.join(self.base_dir, f"{safe}.json")

    def list(self) -> List[str]:
        if not os.path.isdir(self.base_dir):
            return []
        return sorted(
            os.path.splitext(fn)[0]
            for fn in os.listdir(self.base_dir)
            if fn.endswith(".json")
        )

    def save(self, name: str, data: dict) -> None:
        with open(self._path(name), "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)

    def load(self, name: str) -> Optional[dict]:
        path = self._path(name)
        if not os.path.exists(path):
            return None
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)

    def delete(self, name: str) -> None:
        path = self._path(name)
        if os.path.exists(path):
            os.remove(path)
