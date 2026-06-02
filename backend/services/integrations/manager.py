"""Tracks which integrations are enabled (integrations.json under config home)."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from config.paths import paths
from .base import IntegrationRegistry


def _default_state_path() -> Path:
    return Path(paths["integrations"])


class IntegrationsManager:
    def __init__(self, state_path: Path | None = None):
        self.state_path = state_path or _default_state_path()

    def _load(self) -> dict[str, dict[str, Any]]:
        if not self.state_path.exists():
            return {}
        try:
            return json.loads(self.state_path.read_text())
        except json.JSONDecodeError:
            return {}

    def _save(self, state: dict[str, dict[str, Any]]) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.state_path.write_text(json.dumps(state, indent=2) + "\n")

    def is_enabled(self, name: str) -> bool:
        integration = IntegrationRegistry.get(name)
        if integration is None:
            return False
        state = self._load()
        return state.get(name, {}).get("enabled", integration.default_enabled)

    def set_enabled(self, name: str, enabled: bool) -> None:
        if IntegrationRegistry.get(name) is None:
            raise ValueError(f"unknown integration: {name}")
        state = self._load()
        state.setdefault(name, {})["enabled"] = enabled
        self._save(state)

    def list_all(self) -> list[dict[str, Any]]:
        return [
            {**inst.describe(), "enabled": self.is_enabled(name)}
            for name, inst in IntegrationRegistry.all().items()
        ]
