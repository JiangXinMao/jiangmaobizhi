from __future__ import annotations

import json
from pathlib import Path

from .models import AppState


class StateStore:
    def __init__(self, path: Path):
        self.path = Path(path)

    def load(self) -> AppState:
        try:
            return AppState.from_dict(json.loads(self.path.read_text(encoding="utf-8")))
        except (FileNotFoundError, OSError, ValueError, TypeError, json.JSONDecodeError):
            return AppState()

    def save(self, state: AppState) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(f"{self.path.name}.tmp")
        temporary.write_text(
            json.dumps(state.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temporary.replace(self.path)

