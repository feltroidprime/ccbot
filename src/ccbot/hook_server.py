"""HTTP server for receiving remote SessionStart hook notifications.

Provides a lightweight aiohttp application with two endpoints:
- GET /health  — liveness check, returns {"status": "ok"}
- POST /hook   — accepts SessionStart payloads and invokes the registered callback

Key components:
- create_hook_app: builds and returns the aiohttp Application
- start_hook_server: sets up, starts, and returns a runner for lifecycle management
- HookCallback: type alias for the async callable invoked on each valid hook POST
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from aiohttp import web

logger = logging.getLogger(__name__)
HookCallback = Callable[[dict[str, Any]], Awaitable[None]]


def create_hook_app(on_hook: HookCallback) -> web.Application:
    """Create the aiohttp application for receiving hook POSTs."""
    app = web.Application()

    async def health(request: web.Request) -> web.Response:
        return web.json_response({"status": "ok"})

    async def hook(request: web.Request) -> web.Response:
        try:
            payload = await request.json()
        except Exception:
            return web.json_response({"error": "invalid JSON"}, status=400)

        session_id = payload.get("session_id", "")
        if not session_id:
            return web.json_response({"error": "missing session_id"}, status=400)

        logger.info(
            "Hook received: machine=%s window=%s session=%s",
            payload.get("machine_id"),
            payload.get("window_id"),
            session_id,
        )
        try:
            await on_hook(payload)
        except Exception as e:
            logger.error("Hook callback failed: %s", e)
            return web.json_response({"error": "callback failed"}, status=500)

        return web.json_response({"status": "ok"})

    app.router.add_get("/health", health)
    app.router.add_post("/hook", hook)
    return app


async def start_hook_server(
    on_hook: HookCallback,
    host: str = "0.0.0.0",
    port: int = 8080,
) -> web.AppRunner:
    """Start the hook HTTP server. Returns the runner for later cleanup."""
    app = create_hook_app(on_hook)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host, port)
    await site.start()
    logger.info("Hook server listening on %s:%d", host, port)
    return runner
