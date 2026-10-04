"""Raahi Gnani MCP server entrypoint.

ASGI application exposing:
  GET  /health                     - liveness probe
  ANY  /mcp                        - MCP Streamable HTTP endpoint (SDK v2)
  GET  /case-status                - case checklist for Gnani Custom Integrations
  GET  /case-status/{case_id}      - case checklist by case id

Run locally:  uvicorn server:app --host 0.0.0.0 --port ${PORT:-8000}
"""

from __future__ import annotations

import os

from starlette.applications import Starlette

from src.config import get_settings
from src.gnani_tools import mcp
from src.logging_utils import setup_logging

setup_logging(get_settings().log_level)


def create_app() -> Starlette:
    settings = get_settings()
    return mcp.streamable_http_app(
        streamable_http_path="/mcp",
        transport_security=settings.transport_security(),
        host="0.0.0.0",
        max_request_body_size=settings.mcp_max_body_bytes,
    )


app = create_app()

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "server:app",
        host="0.0.0.0",
        port=int(os.environ.get("PORT", "8000")),
    )
