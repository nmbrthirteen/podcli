"""Integration base class + central registry."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Callable, Literal

Category = Literal["editor_export", "platform_upload", "productivity", "ai_helper"]


@dataclass
class ToolSpec:
    name: str
    description: str
    handler: Callable[[dict[str, Any]], dict[str, Any]]
    input_schema: dict[str, Any]
    tags: list[str] = field(default_factory=list)


class IntegrationBase(ABC):
    name: str
    category: Category
    description: str = ""
    default_enabled: bool = False

    @abstractmethod
    def tools(self) -> list[ToolSpec]: ...

    def describe(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "category": self.category,
            "description": self.description,
            "default_enabled": self.default_enabled,
            "tools": [
                {"name": t.name, "description": t.description, "tags": t.tags}
                for t in self.tools()
            ],
        }


class IntegrationRegistry:
    _instances: dict[str, IntegrationBase] = {}

    @classmethod
    def register(cls, integration: IntegrationBase) -> None:
        cls._instances[integration.name] = integration

    @classmethod
    def get(cls, name: str) -> IntegrationBase | None:
        return cls._instances.get(name)

    @classmethod
    def all(cls) -> dict[str, IntegrationBase]:
        return dict(cls._instances)

    @classmethod
    def by_category(cls, cat: Category) -> list[IntegrationBase]:
        return [i for i in cls._instances.values() if i.category == cat]

    @classmethod
    def all_tools(cls) -> list[ToolSpec]:
        out: list[ToolSpec] = []
        for inst in cls._instances.values():
            out.extend(inst.tools())
        return out
