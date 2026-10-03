import json
import os
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import httpx2 as httpx
import pytest

ROOT = Path(__file__).resolve().parents[1]

FORBIDDEN_STRINGS = ("Fast" + "MCP", "mcp.server." + "fastmcp")
SKIP_DIRS = {".venv", ".git", "__pycache__", ".pytest_cache", "htmlcov", "node_modules"}
SKIP_SUFFIXES = {".pyc", ".pdf", ".png", ".jpg", ".wav", ".mp3"}


def test_server_import():
    import server

    assert server.app is not None
    paths = [getattr(route, "path", None) for route in server.app.routes]
    assert "/health" in paths
    assert "/mcp" in paths
    assert "/case-status" in paths


async def test_health_endpoint_in_process():
    import server

    transport = httpx.ASGITransport(app=server.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"ok": True, "service": "raahi-gnani-mcp"}


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture(scope="session")
def live_server():
    port = _free_port()
    env = os.environ.copy()
    env["PORT"] = str(port)
    env.setdefault("GNANI_API_KEY", "live-test-key-not-real")
    env["MCP_ENABLE_DNS_REBINDING_PROTECTION"] = "true"
    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "server:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
        ],
        cwd=str(ROOT),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    base_url = f"http://127.0.0.1:{port}"
    deadline = time.time() + 30
    ready = False
    while time.time() < deadline:
        if proc.poll() is not None:
            _, err = proc.communicate()
            raise RuntimeError(f"server exited early: {err.decode(errors='replace')[-2000:]}")
        try:
            with urllib.request.urlopen(f"{base_url}/health", timeout=1) as response:
                if response.status == 200:
                    ready = True
                    break
        except Exception:
            time.sleep(0.25)
    if not ready:
        proc.kill()
        raise RuntimeError("server did not become healthy in time")
    yield base_url, port
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=5)


def test_startup_on_arbitrary_port(live_server):
    base_url, port = live_server
    assert port > 0
    with urllib.request.urlopen(f"{base_url}/health", timeout=5) as response:
        assert response.status == 200
        payload = json.loads(response.read().decode())
    assert payload == {"ok": True, "service": "raahi-gnani-mcp"}


def _parse_mcp_response(response: httpx.Response) -> dict:
    content_type = response.headers.get("content-type", "")
    if "text/event-stream" in content_type:
        for line in response.text.splitlines():
            if line.startswith("data:"):
                return json.loads(line[len("data:") :].strip())
        raise AssertionError("no data event in SSE response")
    return response.json()


async def test_mcp_endpoint_starts(live_server):
    base_url, _ = live_server
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-11-25",
            "capabilities": {},
            "clientInfo": {"name": "raahi-smoke", "version": "0.0.1"},
        },
    }
    async with httpx.AsyncClient(timeout=15) as client:
        response = await client.post(
            f"{base_url}/mcp",
            json=payload,
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json, text/event-stream",
            },
        )
    assert response.status_code == 200
    body = _parse_mcp_response(response)
    assert body["result"]["serverInfo"]["name"] == "raahi-gnani-mcp"


async def test_mcp_tool_discovery_over_http(live_server):
    from mcp.client.session import ClientSession
    from mcp.client.streamable_http import streamable_http_client

    base_url, _ = live_server
    async with streamable_http_client(f"{base_url}/mcp") as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            init = await session.initialize()
            assert init.server_info.name == "raahi-gnani-mcp"
            listing = await session.list_tools()
    names = sorted(tool.name for tool in listing.tools)
    assert names == sorted(
        [
            "transcribe_speech",
            "speak_reply",
            "call_bank_rm_or_desk",
            "read_call_outcome",
            "navigate_ivr",
            "pull_case_status_into_call",
        ]
    )
    descriptions = {tool.name: (tool.description or "").lower() for tool in listing.tools}
    assert "never guess" in descriptions["transcribe_speech"]
    assert "destination, date or amount" in descriptions["transcribe_speech"]
    assert "unverified" in descriptions["speak_reply"]
    assert "unapproved" in descriptions["call_bank_rm_or_desk"]
    assert "not itself proof" in descriptions["read_call_outcome"]
    assert "never guess unmapped menu options" in descriptions["navigate_ivr"]
    assert "bounded timeout" in descriptions["pull_case_status_into_call"]
    schemas = {tool.name: tool.input_schema for tool in listing.tools}
    assert set(schemas["transcribe_speech"]["required"]) == {"audio", "language"}
    assert set(schemas["call_bank_rm_or_desk"]["required"]) == {
        "phone",
        "country_code",
        "name",
        "checklist_item_id",
    }
    assert set(schemas["read_call_outcome"]["required"]) == {"conversation_id"}


def test_exactly_six_tools_discovered_in_process():
    from mcp.client import Client

    from src.gnani_tools import TOOL_NAMES, mcp

    async def run():
        async with Client(mcp) as client:
            listing = await client.list_tools()
        return sorted(tool.name for tool in listing.tools)

    import asyncio

    names = asyncio.run(run())
    assert names == sorted(TOOL_NAMES)
    assert len(names) == 6


def test_repository_has_no_v1_mcp_api_strings():
    offenders = []
    for path in ROOT.rglob("*"):
        if not path.is_file():
            continue
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        if path.suffix in SKIP_SUFFIXES:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for needle in FORBIDDEN_STRINGS:
            if needle in text:
                offenders.append(f"{path.relative_to(ROOT)}: {needle}")
    assert offenders == [], f"forbidden MCP v1 strings found: {offenders}"


def test_env_file_is_ignored():
    gitignore = (ROOT / ".gitignore").read_text(encoding="utf-8")
    assert ".env" in gitignore.splitlines()
    assert ".venv/" in gitignore.splitlines()
    assert (ROOT / ".env.example").exists()
    example = (ROOT / ".env.example").read_text(encoding="utf-8")
    assert "GNANI_API_KEY=your-gnani-api-key-placeholder" in example


def test_render_yaml_is_valid():
    render = (ROOT / "render.yaml").read_text(encoding="utf-8")
    assert "runtime: python-3.12" in render
    assert "buildCommand: pip install -r requirements.txt" in render
    assert "startCommand: uvicorn server:app --host 0.0.0.0 --port $PORT" in render
    assert "healthCheckPath: /health" in render
