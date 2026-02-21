"""Tests for the hook HTTP server."""

import pytest
from unittest.mock import AsyncMock
from aiohttp.test_utils import TestClient, TestServer
from ccbot.hook_server import create_hook_app


@pytest.mark.asyncio
async def test_health_returns_ok():
    app = create_hook_app(on_hook=AsyncMock())
    async with TestClient(TestServer(app)) as client:
        resp = await client.get("/health")
        assert resp.status == 200
        data = await resp.json()
        assert data["status"] == "ok"


@pytest.mark.asyncio
async def test_hook_post_calls_callback():
    received = []

    async def on_hook(payload):
        received.append(payload)

    app = create_hook_app(on_hook=on_hook)
    payload = {
        "session_id": "abc12345-def4-5678-ghij-klmnopqrstuv",
        "cwd": "/home/user/projects",
        "hook_event_name": "SessionStart",
        "machine_id": "fedora",
        "window_id": "@3",
        "window_name": "my-project",
    }
    async with TestClient(TestServer(app)) as client:
        resp = await client.post("/hook", json=payload)
        assert resp.status == 200
        assert len(received) == 1
        assert received[0]["machine_id"] == "fedora"


@pytest.mark.asyncio
async def test_hook_post_missing_session_id_returns_400():
    app = create_hook_app(on_hook=AsyncMock())
    async with TestClient(TestServer(app)) as client:
        resp = await client.post("/hook", json={"cwd": "/path"})
        assert resp.status == 400


@pytest.mark.asyncio
async def test_hook_post_invalid_json_returns_400():
    app = create_hook_app(on_hook=AsyncMock())
    async with TestClient(TestServer(app)) as client:
        resp = await client.post(
            "/hook", data="not json", headers={"Content-Type": "application/json"}
        )
        assert resp.status == 400


@pytest.mark.asyncio
async def test_hook_callback_exception_returns_500():
    async def failing_hook(payload):
        raise RuntimeError("boom")

    app = create_hook_app(on_hook=failing_hook)
    async with TestClient(TestServer(app)) as client:
        resp = await client.post(
            "/hook", json={"session_id": "abc12345-x", "machine_id": "fedora"}
        )
        assert resp.status == 500
