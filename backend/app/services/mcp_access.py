"""Local keyless MCP and authenticated remote access; never expose /api."""
import hmac
import secrets
from urllib.parse import urlsplit, urlunsplit

from app.core.database import SessionLocal
from app.core.secrets import get_secret_store
from app.models import MCPSettings
from fastapi import HTTPException
from pydantic import AnyHttpUrl
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
    try:
        # urlsplit strips some control characters; reject them before parsing.
        if any(character.isspace() or ord(character) < 32 or ord(character) == 127
               or character == "\\" for character in value):
            raise ValueError("Invalid URL characters")
        parts = urlsplit(value)
        if normalized_origin(f"{parts.scheme}://{parts.netloc}") is None:
            raise ValueError("Invalid authority")
    except ValueError as exc:
        raise HTTPException(422, "Remote MCP URL has an invalid host or port") from exc
    if (parts.scheme != "https" or parts.username is not None or parts.password is not None
            or "?" in value or "#" in value):
        raise HTTPException(422, "Remote MCP URL must be an HTTPS URL without credentials, query or fragment")
    if parts.path not in {"/mangaflow", "/mcp", "/mangaflow/", "/mcp/"}:
        raise HTTPException(422, "Remote MCP URL must end in /mangaflow or /mcp")
    hostname = normalized_origin(f"{parts.scheme}://{parts.netloc}")[1]
    authority = f"[{hostname}]" if ":" in hostname else hostname
    if parts.port:
        authority += f":{parts.port}"
    return urlunsplit(("https", authority, parts.path.rstrip("/"), "", ""))


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


def normalized_origin(value):
    """Compare actual origins, including ports, without trusting proxy headers."""
    if not value or any(character.isspace() or ord(character) < 32 or ord(character) == 127
                        or character == "\\" for character in value):
        return None
    try:
        parts = urlsplit(value)
        if (parts.scheme not in {"http", "https"} or not parts.hostname
                or parts.username is not None or parts.password is not None
                or parts.path or parts.query or parts.fragment or parts.port == 0
                or "%" in parts.netloc or parts.netloc.endswith(":")):
            return None
        # Pydantic and the Node connector use browser-compatible URL/IDNA rules.
        parsed = AnyHttpUrl(value)
        return parsed.scheme, parsed.host.strip("[]"), parsed.port
    except ValueError:
        return None


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
            valid_host = bool(hostname) and authority.port != 0 and not (
                authority.path or authority.query or authority.fragment
                or authority.username is not None or authority.password is not None
                or any(character.isspace() or character == "\\" for character in host))
        except ValueError:
            hostname, valid_host = "", False
        allowed = [value.strip("[]").lower() for value in settings["allowed_hosts"]]
        origin = headers.get(b"origin", b"").decode("latin1")
        origins = [f"{scope.get('scheme', 'http')}://{host}", "http://localhost:5173", *settings["allowed_origins"]]
        if settings["public_url"]:
            parts = urlsplit(settings["public_url"])
            origins.append(f"{parts.scheme}://{parts.netloc}")
        allowed_origins = {value for candidate in origins if (value := normalized_origin(candidate)) is not None}
        request_origin = normalized_origin(origin)
        if (not valid_host or hostname not in allowed
                or (origin and request_origin not in allowed_origins)):
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
