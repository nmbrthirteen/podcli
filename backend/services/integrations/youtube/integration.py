"""YouTube analytics integration — links rendered clips to uploads and pulls
per-video performance (views, retention, CTR) for the learning loop."""
from __future__ import annotations

from typing import Any

from ..base import IntegrationBase, IntegrationRegistry, ToolSpec


class YouTubeIntegration(IntegrationBase):
    name = "youtube"
    category = "ai_helper"
    description = (
        "Performance feedback, not publishing. Links rendered clips to your published "
        "videos and pulls views, retention, and CTR so podcli learns what works and "
        "Claude picks better shorts. Read-only — podcli never uploads. Bring your own "
        "Google OAuth client, or import a YouTube Studio analytics CSV."
    )
    default_enabled = False

    def _sync(self, params: dict[str, Any]) -> dict[str, Any]:
        from . import sync
        csv_path = params.get("csv_path")
        if csv_path:
            return sync.sync_from_csv(csv_path)
        return {"updated": sync.sync_metrics()}

    def tools(self) -> list[ToolSpec]:
        return [
            ToolSpec(
                name="youtube_sync",
                description="Sync YouTube performance onto linked clips (live API, or csv_path for a Studio export).",
                handler=self._sync,
                input_schema={
                    "type": "object",
                    "properties": {"csv_path": {"type": "string"}},
                },
                tags=["analytics", "youtube"],
            ),
        ]


IntegrationRegistry.register(YouTubeIntegration())
