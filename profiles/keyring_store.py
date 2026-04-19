"""
Secure password storage using the OS keychain via the `keyring` library.

  macOS   → Keychain Access
  Windows → Windows Credential Manager
  Linux   → SecretService (GNOME Keyring / KWallet)

Falls back to an in-memory store if keyring is unavailable (e.g. headless CI).
"""
from __future__ import annotations

_SERVICE = "db_exporter"
_fallback: dict[str, str] = {}

try:
    import keyring as _keyring
    _USE_KEYRING = True
except ImportError:
    _keyring = None  # type: ignore
    _USE_KEYRING = False


def get_password(profile_name: str) -> str | None:
    if _USE_KEYRING:
        try:
            return _keyring.get_password(_SERVICE, profile_name)
        except Exception:
            pass
    return _fallback.get(profile_name)


def set_password(profile_name: str, password: str) -> None:
    if _USE_KEYRING:
        try:
            _keyring.set_password(_SERVICE, profile_name, password)
            return
        except Exception:
            pass
    _fallback[profile_name] = password


def delete_password(profile_name: str) -> None:
    if _USE_KEYRING:
        try:
            _keyring.delete_password(_SERVICE, profile_name)
        except Exception:
            pass
    _fallback.pop(profile_name, None)
