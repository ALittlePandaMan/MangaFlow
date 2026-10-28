"""MCP follows the deployed authority without broadening cross-origin access."""
import io

import pytest
from app.services.mcp_access import normalized_origin
from PIL import Image

INITIALIZE = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
    "protocolVersion": "2025-11-25", "capabilities": {}, "clientInfo": {"name": "origin-test", "version": "1"}}}


@pytest.mark.parametrize("authority", ["localhost:18080", "127.0.0.1:19090", "[::1]:28080"])
def test_custom_port_settings_installation_and_file_transfer(client, authority):
    origin = "http://" + authority
    headers = {"Host": authority, "Origin": origin}
    settings = client.get("/api/mcp-settings", headers=headers)
    assert settings.status_code == 200
    saved = client.put("/api/mcp-settings", headers=headers, json={"enabled": True, "public_url": ""})
    assert saved.status_code == 200 and saved.json()["public_url"] == ""
    assert saved.json()["token"] == settings.json()["token"]
    manifest = client.get("/mangaflow/setup.json", headers=headers)
    assert manifest.status_code == 200
    assert manifest.json()["endpoint"] == origin + "/mangaflow"
    assert manifest.json()["installer_url"] == origin + "/mangaflow/install.mjs"
    assert origin + "/mangaflow/setup.json" in client.get("/mangaflow/setup", headers=headers).text
    response = client.post("/mangaflow", headers={**headers, "Accept": "application/json, text/event-stream"}, json=INITIALIZE)
    assert response.status_code == 200 and response.json()["result"]["serverInfo"]["name"] == "MangaFlow"
    image = io.BytesIO()
    Image.new("RGB", (8, 8)).save(image, "PNG")
    uploaded = client.post("/mangaflow/files", headers={**headers, "Content-Type": "image/png"},
                           params={"filename": "page.png"}, content=image.getvalue())
    assert uploaded.status_code == 200, uploaded.text
    assert uploaded.json()["asset_id"]


@pytest.mark.parametrize("origin", ["http://localhost:18081", "https://localhost:18080", "http://127.0.0.1:18080",
                                   "http://evil.invalid:18080", "null", "http://localhost:18080/path"])
def test_other_origins_cannot_use_a_custom_port(client, origin):
    headers = {"Host": "localhost:18080", "Origin": origin}
    for path in ("/api/mcp-settings", "/mangaflow/setup.json"):
        assert client.get(path, headers=headers).status_code == 403
    assert client.post("/mangaflow", headers=headers, json=INITIALIZE).status_code == 403


@pytest.mark.parametrize("host", ["evil.invalid:18080", "localhost:0", "localhost:65536", "localhost:not-a-port",
                                 "user@localhost:18080", "localhost:18080/path"])
def test_bad_hosts_are_rejected_even_with_forged_forwarding_headers(client, host):
    headers = {"Host": host, "X-Forwarded-Host": "localhost:18080", "X-Forwarded-Proto": "http"}
    assert client.get("/mangaflow/setup.json", headers=headers).status_code == 403


def test_normalized_origins_development_and_remote_override(client):
    assert normalized_origin("http://LOCALHOST:80") == normalized_origin("http://localhost")
    assert normalized_origin("https://manga.example:443") == normalized_origin("https://manga.example")
    assert normalized_origin("https://manga.example:9443") != normalized_origin("https://manga.example")
    assert client.options("/mangaflow", headers={"Host": "localhost:18080", "Origin": "http://localhost:5173"}).status_code == 200
    public = "https://manga.example:9443/mangaflow"
    local = {"Host": "localhost:18080", "Origin": "http://localhost:18080"}
    try:
        assert client.put("/api/mcp-settings", headers=local, json={"public_url": public}).status_code == 200
        remote = {"Host": "manga.example:9443", "Origin": "https://manga.example:9443"}
        assert client.get("/mangaflow/setup.json", headers=remote).json()["endpoint"] == public
        assert client.post("/mangaflow", headers=remote, json=INITIALIZE).status_code == 401
        token = client.get("/api/mcp-settings", headers=local).json()["token"]
        protocol_headers = {**remote, "Authorization": "Bearer " + token, "Accept": "application/json, text/event-stream"}
        assert client.post("/mangaflow", headers=protocol_headers, json=INITIALIZE).status_code == 200
    finally:
        client.put("/api/mcp-settings", headers=local, json={"public_url": "", "enabled": True})


@pytest.mark.parametrize("value", ["https://[::1/mangaflow", "https://manga example/mangaflow",
    "https://manga.example\\evil/mangaflow", "\nhttps://manga.example/mangaflow",
    "https://manga.example/manga\tflow", "https://@manga.example/mangaflow",
    "https://manga.example:0/mangaflow", "https://manga.example:65536/mangaflow",
    "https://manga.example:bad/mangaflow", "https://manga.example/mangaflow?",
    "https://manga.example/mangaflow#", "https://manga.example/mangaflow//",
    "https://%65xample.com/mangaflow", "https://manga^example/mangaflow",
    "https://manga.example:/mangaflow"])
def test_invalid_remote_url_returns_422_without_changing_settings(client, value):
    headers = {"Host": "localhost:18080", "Origin": "http://localhost:18080"}
    before = client.get("/api/mcp-settings", headers=headers).json()
    response = client.put("/api/mcp-settings", headers=headers,
                          json={"enabled": not before["enabled"], "public_url": value})
    assert response.status_code == 422, response.text
    assert client.get("/api/mcp-settings", headers=headers).json() == before


def test_remote_international_host_is_saved_in_client_compatible_form(client):
    from app.services.mcp_access import validate_endpoint
    assert validate_endpoint("https://漫画.example:9443/mangaflow/") == "https://xn--qex62k.example:9443/mangaflow"
    assert validate_endpoint("https://faß.example/mangaflow") == "https://xn--fa-hia.example/mangaflow"
    assert validate_endpoint("https://[::1]:9443/mangaflow") == "https://[::1]:9443/mangaflow"
