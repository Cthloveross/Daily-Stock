# -*- coding: utf-8 -*-
"""
Auth middleware: protect /api/v1/* when admin auth is enabled.
"""

from __future__ import annotations

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from src.auth import COOKIE_NAME, is_auth_enabled, verify_session

EXEMPT_PATHS = frozenset({
    "/api/v1/auth/login",
    "/api/v1/auth/status",
    "/api/health",
    "/health",
    "/docs",
    "/redoc",
    "/openapi.json",
})


def _path_exempt(path: str) -> bool:
    """Check if path is exempt from auth."""
    normalized = path.rstrip("/") or "/"
    return normalized in EXEMPT_PATHS


class AuthMiddleware:
    """Pure ASGI auth guard for ``/api/v1/*``.

    Keeping this middleware at the ASGI message layer is important for file and
    streaming responses: downstream body messages pass through unchanged,
    including their original ``more_body`` termination flag.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        if not is_auth_enabled():
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")
        if _path_exempt(path):
            await self.app(scope, receive, send)
            return

        if not path.startswith("/api/v1/"):
            await self.app(scope, receive, send)
            return

        request = Request(scope)
        cookie_val = request.cookies.get(COOKIE_NAME)
        if not cookie_val or not verify_session(cookie_val):
            response = JSONResponse(
                status_code=401,
                content={
                    "error": "unauthorized",
                    "message": "Login required",
                },
            )
            await response(scope, receive, send)
            return

        await self.app(scope, receive, send)


def add_auth_middleware(app):
    """Add auth middleware to protect API routes.

    The middleware is always registered; whether auth is enforced is determined
    at request time by is_auth_enabled() so the decision stays consistent across
    any runtime configuration reload.
    """
    app.add_middleware(AuthMiddleware)
