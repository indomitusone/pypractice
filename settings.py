"""Persistencia de preferencias de usuario y recuperación automática de borradores."""

import json
import os
import sys
from pathlib import Path
from typing import Any

DEFAULT_SETTINGS = {
    "theme": "oscuro",
    "font_family": "Consolas",
    "font_size": 12,
    "safe_mode": True,
    "timeout_seconds": 60,  # 0 significa sin límite
    "interpreter_path": "",
    "script_args": "",
    "use_isolated_mode": True,
    "audio_feedback": False,
    "recent_files": [],
    "completed_exercises": [],
}


def get_app_data_dir() -> Path:
    """Devuelve el directorio de configuración persistente del usuario."""
    if sys.platform == "win32":
        base = os.environ.get("APPDATA") or str(Path.home())
        path = Path(base) / "PyPractice"
    else:
        base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
        path = Path(base) / "pypractice"
    path.mkdir(parents=True, exist_ok=True)
    return path


class SettingsManager:
    def __init__(self, base_dir: Path | None = None):
        self.base_dir = base_dir or get_app_data_dir()
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.config_file = self.base_dir / "config.json"
        self.recovery_file = self.base_dir / "recovery_session.json"
        self.data: dict[str, Any] = dict(DEFAULT_SETTINGS)
        self.load()

    def load(self) -> dict[str, Any]:
        if self.config_file.exists():
            try:
                loaded = json.loads(self.config_file.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    merged = dict(DEFAULT_SETTINGS)
                    for key in DEFAULT_SETTINGS:
                        if key in loaded:
                            merged[key] = loaded[key]
                    self.data = merged
            except Exception:
                self.data = dict(DEFAULT_SETTINGS)
        return self.data

    def save(self) -> None:
        try:
            tmp = self.config_file.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.data, indent=2, ensure_ascii=False), encoding="utf-8")
            tmp.replace(self.config_file)
        except Exception:
            pass

    def get(self, key: str, default: Any = None) -> Any:
        return self.data.get(key, DEFAULT_SETTINGS.get(key, default))

    def set(self, key: str, value: Any, persist: bool = True) -> None:
        self.data[key] = value
        if persist:
            self.save()

    def add_recent_file(self, filepath: str, max_items: int = 10) -> None:
        norm = os.path.abspath(filepath)
        recent = [p for p in self.data.get("recent_files", []) if os.path.abspath(p) != norm]
        recent.insert(0, norm)
        self.data["recent_files"] = recent[:max_items]
        self.save()

    def mark_exercise_completed(self, exercise_id: str) -> None:
        done = list(self.data.get("completed_exercises", []))
        if exercise_id not in done:
            done.append(exercise_id)
            self.data["completed_exercises"] = done
            self.save()

    # --- Recuperación automática (autosave) ---
    def save_recovery_session(self, tabs_state: list[dict[str, Any]]) -> None:
        """Guarda un borrador de pestañas abiertas para recuperación ante cierres inesperados."""
        try:
            payload = {"tabs": tabs_state}
            tmp = self.recovery_file.with_suffix(".tmp")
            tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            tmp.replace(self.recovery_file)
        except Exception:
            pass

    def load_recovery_session(self) -> list[dict[str, Any]]:
        if not self.recovery_file.exists():
            return []
        try:
            payload = json.loads(self.recovery_file.read_text(encoding="utf-8"))
            tabs = payload.get("tabs", [])
            if isinstance(tabs, list):
                return [t for t in tabs if isinstance(t, dict) and "content" in t]
        except Exception:
            pass
        return []

    def clear_recovery_session(self) -> None:
        try:
            if self.recovery_file.exists():
                self.recovery_file.unlink()
        except Exception:
            pass
