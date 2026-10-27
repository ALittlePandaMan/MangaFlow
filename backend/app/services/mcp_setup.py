"""Public, credential-free bootstrap material; never exposes business APIs."""
import gzip
import hashlib
import io
import json
import sys
import tarfile
from pathlib import Path

from app.core.config import REPOSITORY_ROOT
from app.services.mcp_access import read_settings
from fastapi import APIRouter, Request
from fastapi.responses import PlainTextResponse, Response

router = APIRouter(tags=["mcp-setup"])
CONNECTOR_FILES = ("index.mjs", "directory-import.mjs", "package.json", "package-lock.json", "README.md")
BOOTSTRAP_FILES = frozenset(f"/{prefix}/{name}" for prefix in ("mangaflow", "mcp")
    for name in ("setup", "setup.json", "install.mjs", "connector.tgz"))


def connector_directory():
    directory = REPOSITORY_ROOT / "connectors" / "mangaflow-mcp"
    return directory if directory.is_dir() else Path(sys.prefix) / "share" / "mangaflow" / "mcp-connector"


def connector_package():
    """Deterministic, fixed-file tarball accepted by the standalone installer."""
    directory = connector_directory()
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w", format=tarfile.USTAR_FORMAT) as archive:
        for name in CONNECTOR_FILES:
            contents = (directory / name).read_bytes()
            entry = tarfile.TarInfo("package/" + name)
            entry.size, entry.mode = len(contents), 0o644
            archive.addfile(entry, io.BytesIO(contents))
    return gzip.compress(output.getvalue(), mtime=0)


def setup_endpoint(request: Request):
    return read_settings()["public_url"] or str(request.base_url).rstrip("/") + "/mangaflow"


@router.get("/mcp/setup.json")
@router.get("/mangaflow/setup.json")
def setup_manifest(request: Request):
    package = connector_package()
    metadata = json.loads((connector_directory() / "package.json").read_text())
    endpoint = setup_endpoint(request)
    return {"name": "mangaflow-mcp", "version": metadata["version"], "node": ">=20.11",
        "endpoint": endpoint, "package_path": "/mangaflow/connector.tgz",
        "sha256": hashlib.sha256(package).hexdigest(), "size": len(package),
        "installer_url": endpoint + "/install.mjs", "guide_url": endpoint + "/setup"}


@router.get("/mcp/connector.tgz")
@router.get("/mangaflow/connector.tgz")
def bootstrap_package():
    return Response(connector_package(), media_type="application/gzip", headers={
        "Content-Disposition": 'attachment; filename="mangaflow-mcp.tgz"', "Cache-Control": "no-cache"})


@router.get("/mcp/install.mjs")
@router.get("/mangaflow/install.mjs")
def bootstrap_installer():
    return Response((connector_directory() / "install.mjs").read_bytes(), media_type="text/javascript",
        headers={"Content-Disposition": 'attachment; filename="mangaflow-install.mjs"', "Cache-Control": "no-cache"})


@router.get("/mcp/setup", response_class=PlainTextResponse)
@router.get("/mangaflow/setup", response_class=PlainTextResponse)
def setup_guide(request: Request):
    endpoint = setup_endpoint(request)
    return PlainTextResponse(f"""# Install MangaFlow MCP in the current Agent

This is one-time client setup requested by the user, before the translation workflow.
Install the OFFICIAL LOCAL STDIO CONNECTOR on the computer where this Agent reads files,
even when MangaFlow is hosted remotely. Do not configure direct HTTP as a substitute:
it lacks local_import/local_download.

1. Preserve existing MCP servers and unrelated client settings. Inspect this client's
   supported configuration or native MCP-add command; do not guess another client's paths.
2. Check Node.js >=20.11 and npm. Reuse an installed runtime. If missing, install a
   supported Node.js LTS through the platform's official installer/package manager if
   permitted. If terminal/configuration access is unavailable, report this specific
   prerequisite; the remote service cannot install software on the client's computer.
3. Download {endpoint}/install.mjs to a temporary local .mjs file using the client's
   native file/network/terminal tools. Run the downloaded official installer:

   node <absolute-path-to-downloaded-install.mjs> --url {json.dumps(endpoint)}

   It downloads setup.json and a checksum-verified fixed-file package from this same
   MangaFlow service, installs pinned dependencies with npm ci --ignore-scripts into
   ~/.mangaflow/mcp-connector, and verifies the connector via the official MCP SDK.
   It does not require a global npm install or administrator rights, and does not
   install MangaFlow's backend, AI models or image-processing libraries. Run it again
   to reuse an intact installation; do not repeatedly install it for each comic.
4. Read the installer's JSON result. Use server.command (absolute Node executable),
   server.args (absolute index.mjs) and server.env in THIS client's stdio MCP format.
   These absolute paths do not rely on a shell PATH or Windows .cmd resolution.
   The result also includes a generic mcpServers JSON example. Convert only the
   configuration format if the current client requires TOML or another schema.
   Do not overwrite other MCP servers. Do not put secrets in a prompt or source control.
   Local /mangaflow needs no key. Remote HTTPS (or /mcp) requires the existing
   MANGAFLOW_MCP_TOKEN environment variable; install material contains no access key.
5. Reload/reconnect using the client's supported mechanism. If this requires the user
   to restart the app/session, finish the installation/configuration and report just
   this remaining step. Do not claim that the current session has reloaded tools.
6. Through the newly configured connection, call mcp_status and verify
   connection_capabilities.transport == "stdio" and local_import,
   local_download are true. The installer's isolated protocol probe
   is not proof that the current Agent session has reconnected.
7. Once connected, a user-provided image folder goes DIRECTLY to local_import;
   it creates a project named after the folder. Poll local_transfer_status, then wait
   for result.job.id with task_wait(kind="file") and verify pages. Continue using
   workflow_get. Do not ask for manual web upload or write transfer,
   image-processing or translation scripts. Installation commands belong only here.

Package metadata: {endpoint}/setup.json
Server endpoint: {endpoint}
Public install files contain only official connector code/metadata, never project
data or credentials. Business tools and transfers retain their existing authentication.
""", media_type="text/markdown", headers={"Cache-Control": "no-cache"})
