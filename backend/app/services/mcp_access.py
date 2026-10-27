"""Local keyless MCP and authenticated remote access; never expose /api."""
import hmac
import secrets
from urllib.parse import urlsplit

from app.core.database import SessionLocal
from app.core.secrets import get_secret_store
from app.models import MCPSettings
from fastapi import HTTPException
from sqlalchemy.dialects.postgresql import insert
from starlette.responses import JSONResponse


def read_settings():
    with SessionLocal() as db:
        row = db.get(MCPSettings, 1)
        if row is None:
            db.execute(insert(MCPSettings).values(id=1, enabled=True,
                encrypted_token=get_secret_store().encrypt(secrets.token_urlsafe(32)),
                public_url="", allowed_hosts=["localhost", "127.0.0.1", "[::1]"], allowed_origins=[])
                .on_conflict_do_nothing(index_elements=["id"]))
            db.commit()
            row = db.get(MCPSettings, 1)
        return {"enabled": row.enabled, "public_url": row.public_url,
            "allowed_hosts": row.allowed_hosts, "allowed_origins": row.allowed_origins,
            "token": get_secret_store().decrypt(row.encrypted_token)}


def validate_endpoint(value):
    if not value:
        return ""
    parts = urlsplit(value)
    try:
        if parts.port == 0:
            raise ValueError("Port must be positive")
    except ValueError as exc:
        raise HTTPException(422, "Remote MCP URL has an invalid port") from exc
    if parts.scheme != "https" or not parts.hostname or parts.username or parts.password or parts.query or parts.fragment:
        raise HTTPException(422, "Remote MCP URL must be an HTTPS URL without credentials, query or fragment")
    if parts.path.rstrip("/") not in {"/mangaflow", "/mcp"}:
        raise HTTPException(422, "Remote MCP URL must end in /mangaflow or /mcp")
    return value.rstrip("/")


def write_settings(values, rotate=False):
    read_settings()
    with SessionLocal() as db:
        row = db.get(MCPSettings, 1, with_for_update=True)
        if "public_url" in values:
            value = validate_endpoint(values["public_url"])
            row.public_url = value
            # Exact host, no wildcard host/origin allowance.
            row.allowed_hosts = ["localhost", "127.0.0.1", "[::1]"] + ([urlsplit(value).hostname] if value else [])
        if "enabled" in values:
            row.enabled = values["enabled"]
        if rotate:
            row.encrypted_token = get_secret_store().encrypt(secrets.token_urlsafe(32))
        db.commit()
    return read_settings()


class MCPAccessMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        path = scope.get("path", "")
        from app.core.config import get_settings
        control = path.startswith(get_settings().api_prefix + "/mcp-settings")
        canonical = path == "/mangaflow" or path.startswith("/mangaflow/")
        if scope["type"] != "http" or not (control or canonical or path == "/mcp" or path.startswith("/mcp/")):
            return await self.app(scope, receive, send)
        from starlette.concurrency import run_in_threadpool
        settings = await run_in_threadpool(read_settings)
        headers = dict(scope.get("headers", []))
        host = headers.get(b"host", b"").decode("latin1").lower()
        try:
            authority = urlsplit("//" + host)
            hostname = authority.hostname or ""
            valid_host = not (authority.path or authority.query or authority.fragment or authority.username or authority.password)
        except ValueError:
            hostname, valid_host = "", False
        allowed = [value.strip("[]").lower() for value in settings["allowed_hosts"]]
        origin = headers.get(b"origin", b"").decode("latin1")
        local_origins = {"http://localhost:5173", "http://localhost:8080", "http://127.0.0.1:8080"}
        if settings["public_url"]:
            parts = urlsplit(settings["public_url"])
            local_origins.add(f"{parts.scheme}://{parts.netloc}")
        if not valid_host or hostname not in allowed or (origin and origin not in local_origins | set(settings["allowed_origins"])):
            return await JSONResponse({"error": "invalid_origin_or_host"}, 403)(scope, receive, send)
        if control:
            return await self.app(scope, receive, send)
        # Only fixed installation material is public, including at the remote
        # MCP-only ingress. Host/Origin protection above still applies.
        from app.services.mcp_setup import BOOTSTRAP_FILES
        if path in BOOTSTRAP_FILES and scope.get("method") in {"GET", "HEAD"}:
            return await self.app(scope, receive, send)
        if scope.get("method") == "OPTIONS":
            return await JSONResponse({}, headers={"Access-Control-Allow-Origin": origin,
                "Access-Control-Allow-Methods": "POST, GET, DELETE, OPTIONS",
                "Access-Control-Allow-Headers": "Authorization, Content-Type, Accept, MCP-Protocol-Version, MCP-Session-Id, Last-Event-ID",
                "Access-Control-Max-Age": "600", "Vary": "Origin"})(scope, receive, send)
        if not settings["enabled"]:
            return await JSONResponse({"error": "mcp_disabled"}, 503)(scope, receive, send)
        authorization = headers.get(b"authorization", b"")
        # The normal Docker app stays bound to loopback. Keyless access is
        # restricted to its canonical local endpoint; configuring remote ingress
        # keeps authentication mandatory, even when its proxy rewrites Host.
        local_anonymous = canonical and hostname in {"localhost", "127.0.0.1", "::1"} and not settings["public_url"]
        if (authorization and not hmac.compare_digest(authorization, ("Bearer " + settings["token"]).encode())) or (not authorization and not local_anonymous):
            return await JSONResponse({"error": "unauthorized"}, 401,
                headers={"WWW-Authenticate": "Bearer"})(scope, receive, send)
        async def cors_send(message):
            if origin and message["type"] == "http.response.start":
                message = {**message, "headers": [(key, value) for key, value in message.get("headers", [])
                    if key.lower() not in {b"access-control-allow-origin", b"access-control-expose-headers"}]
                    + [(b"access-control-allow-origin", origin.encode("latin1")),
                       (b"access-control-expose-headers", b"MCP-Session-Id, MCP-Protocol-Version")]}
            await send(message)
        return await self.app(scope, receive, cors_send)
