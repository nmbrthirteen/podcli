"""DaVinci Resolve integration — exposes the export_to_davinci_resolve MCP tool."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from ..base import IntegrationBase, ToolSpec
from .._shared.media_probe import probe_media
from .._shared.timeline_ir import CaptionLayer, MediaClip, Project, Short
from . import emitter


class DaVinciResolveIntegration(IntegrationBase):
    name = "davinci_resolve"
    category = "editor_export"
    description = (
        "Export podcli shorts as a DaVinci Resolve FCPXML — each short becomes an "
        "editable compound clip (source on V1, ProRes 4444 alpha captions on V2). "
        "Works in free + Studio Resolve 20.x."
    )
    default_enabled = False

    def tools(self) -> list[ToolSpec]:
        return [
            ToolSpec(
                name="export_to_davinci_resolve",
                description=(
                    "Export podcli shorts as a DaVinci Resolve FCPXML project. "
                    "Each short becomes a compound clip on the master timeline; "
                    "source video and ProRes 4444 alpha caption overlay land on "
                    "separate layers inside the compound so they remain editable. "
                    "Works in free and Studio Resolve 20.x."
                ),
                handler=self._handle_export,
                input_schema={
                    "type": "object",
                    "properties": {
                        "project_name": {"type": "string"},
                        "output_path": {"type": "string"},
                        "fps": {"type": "number", "default": 30},
                        "width": {"type": "integer", "default": 1080},
                        "height": {"type": "integer", "default": 1920},
                        "shorts": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "title": {"type": "string"},
                                    "source_path": {"type": "string"},
                                    "captions_path": {"type": "string"},
                                    "logo_path": {"type": "string"},
                                },
                                "required": ["title", "source_path"],
                            },
                        },
                    },
                    "required": ["project_name", "output_path", "shorts"],
                },
                tags=["editor", "export", "davinci", "fcpxml"],
            ),
        ]

    def _handle_export(self, params: dict[str, Any]) -> dict[str, Any]:
        if not params.get("shorts"):
            raise ValueError("export_to_davinci_resolve requires at least one short")
        shorts: list[Short] = []
        for s in params["shorts"]:
            src_info = probe_media(s["source_path"])
            source = MediaClip(
                path=Path(s["source_path"]).resolve(),
                fps=src_info["fps"],
                duration_frames=src_info["duration_frames"],
                width=src_info["width"],
                height=src_info["height"],
                has_audio=src_info["has_audio"],
                audio_channels=src_info["audio_channels"],
            )
            shorts.append(Short(
                title=s["title"],
                source=source,
                captions=_maybe_layer(s.get("captions_path")),
                logo=_maybe_layer(s.get("logo_path")),
            ))

        first = shorts[0].source if shorts else None
        project = Project(
            name=params["project_name"],
            fps=float(params["fps"]) if params.get("fps") is not None else (first.fps if first else 30.0),
            width=int(params["width"]) if params.get("width") is not None else (first.width if first else 1080),
            height=int(params["height"]) if params.get("height") is not None else (first.height if first else 1920),
            shorts=shorts,
        )
        out_path = Path(params["output_path"]).resolve()
        emitter.emit(project, out_path)
        return {
            "fcpxml_path": str(out_path),
            "shorts_count": len(shorts),
            "format": f"{project.width}x{project.height}@{project.fps}fps",
        }


def _maybe_layer(p: str | None) -> CaptionLayer | None:
    if not p:
        return None
    info = probe_media(p)
    return CaptionLayer(
        path=Path(p).resolve(),
        fps=info["fps"],
        duration_frames=info["duration_frames"],
    )


integration = DaVinciResolveIntegration()
