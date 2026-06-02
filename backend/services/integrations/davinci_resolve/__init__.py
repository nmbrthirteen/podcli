"""DaVinci Resolve integration — emits FCPXML 1.10 for free + Studio Resolve 20.x."""
from ..base import IntegrationRegistry
from .integration import integration
from . import emitter

IntegrationRegistry.register(integration)

__all__ = ["integration", "emitter"]
