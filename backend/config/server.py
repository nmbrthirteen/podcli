from __future__ import annotations

import os
from typing import Mapping

DEFAULT_PORT = 3847


def resolve_web_server_port(env: Mapping[str, str] | None = None) -> int:
    """Mirror of resolveWebServerPort in src/config/server.ts. The Python launcher
    and the Node studio must agree on the port or the CLI prints a dead URL."""
    env = os.environ if env is None else env
    raw = env.get("PODCLI_PORT") or env.get("PORT")
    try:
        port = int(str(raw).strip())
    except (TypeError, ValueError):
        return DEFAULT_PORT
    return port if 0 < port <= 65535 else DEFAULT_PORT


def web_server_url(env: Mapping[str, str] | None = None) -> str:
    return f"http://localhost:{resolve_web_server_port(env)}"
