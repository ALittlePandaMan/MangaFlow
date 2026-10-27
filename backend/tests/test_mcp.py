import io
from uuid import uuid4

import pytest
from PIL import Image


@pytest.fixture
def client(client):
    client.headers['Host'] = 'localhost'
    return client


def rpc(client, name, arguments=None, token=None):
    token = token or client.get('/api/mcp-settings').json()['token']
    response = client.post('/mcp', headers={'Host': 'localhost', 'Authorization': f'Bearer {token}',
        'Accept': 'application/json, text/event-stream'}, json={'jsonrpc': '2.0', 'id': 1,
        'method': 'tools/call', 'params': {'name': name, 'arguments': arguments or {}}})
    assert response.status_code == 200, response.text
    return response.json()['result']


def page(client):
    project = client.post('/api/projects', json={'name': 'MCP 测试'}).json()
    data = io.BytesIO()
    Image.new('RGB', (300, 200), 'white').save(data, 'PNG')
    image = client.post(f"/api/projects/{project['id']}/images", files={'files': ('漫画.png', data.getvalue(), 'image/png')}).json()[0]
    return project, image


def mutation(**kwargs):
    return {'operation_id': str(uuid4()), **kwargs}


def test_discovery_and_credentials(client):
    token = client.get('/api/mcp-settings').json()['token']
    headers = {'Host': 'localhost', 'Authorization': f'Bearer {token}', 'Accept': 'application/json, text/event-stream'}
    response = client.post('/mcp', headers=headers, json={'jsonrpc': '2.0', 'id': 1, 'method': 'initialize',
        'params': {'protocolVersion': '2025-11-25', 'capabilities': {}, 'clientInfo': {'name': 'test', 'version': '1'}}})
    assert response.status_code == 200, response.text
    response = client.post('/mcp', headers=headers, json={'jsonrpc': '2.0', 'id': 2, 'method': 'tools/list'})
    tools = response.json()['result']['tools']
    assert len(tools) == 20
    assert len({tool['name'] for tool in tools}) == len(tools)
    assert all(tool['inputSchema']['type'] == 'object' for tool in tools)
    import re

    from jsonschema import Draft202012Validator
    for tool in tools:
        assert re.fullmatch(r'[A-Za-z0-9_.-]{1,128}', tool['name'])
        Draft202012Validator.check_schema(tool['inputSchema'])
    assert 'token' not in rpc(client, 'mcp_status')['structuredContent']
    assert client.post('/mcp', headers={'Host': 'localhost'}).status_code == 401
    assert client.post('/mcp', headers={**headers, 'Origin': 'https://evil.invalid'}).status_code == 403
    assert client.post('/mcp', headers={**headers, 'Host': 'evil.invalid'}).status_code == 403
    preflight = client.options('/mcp', headers={'Host':'localhost', 'Origin':'http://localhost:8080',
        'Access-Control-Request-Method':'POST','Access-Control-Request-Headers':'authorization,content-type'})
    assert preflight.status_code == 200
    assert preflight.headers['access-control-allow-origin'] == 'http://localhost:8080'
    assert client.get('/api/mcp-settings', headers={'Host':'evil.invalid'}).status_code == 403
    client.post('/api/mcp-settings/rotate')
    assert client.post('/mcp', headers=headers).status_code == 401
    client.put('/api/mcp-settings', json={'enabled': False})
    assert client.post('/mcp', headers=headers).status_code == 503
    client.put('/api/mcp-settings', json={'enabled': True})


def test_keyless_local_endpoint_preserves_origin_and_remote_auth_checks(client):
    payload = {'jsonrpc': '2.0', 'id': 1, 'method': 'tools/list'}
    headers = {'Host': 'localhost:8080', 'Accept': 'application/json, text/event-stream'}
    response = client.post('/mangaflow', headers=headers, json=payload)
    assert response.status_code == 200, response.text
    assert len(response.json()['result']['tools']) == 20
    assert client.post('/mangaflow', headers={**headers, 'Host': 'evil.invalid'}, json=payload).status_code == 403
    assert client.post('/mangaflow', headers={**headers, 'Origin': 'https://evil.invalid'}, json=payload).status_code == 403
    assert client.post('/mangaflow', headers={**headers, 'Authorization': 'Bearer invalid'}, json=payload).status_code == 401
    client.put('/api/mcp-settings', json={'enabled': False})
    assert client.post('/mangaflow', headers=headers, json=payload).status_code == 503
    client.put('/api/mcp-settings', json={'enabled': True})
    try:
        changed = client.put('/api/mcp-settings', json={'public_url': 'https://manga.example/mangaflow'})
        assert changed.status_code == 200
        assert client.post('/mangaflow', headers={**headers, 'Host': 'manga.example'}, json=payload).status_code == 401
        # A remote reverse proxy must not bypass auth by rewriting Host to localhost.
        assert client.post('/mangaflow', headers=headers, json=payload).status_code == 401
        token = changed.json()['token']
        assert client.post('/mangaflow', headers={**headers, 'Host': 'manga.example', 'Authorization': f'Bearer {token}'}, json=payload).status_code == 200
    finally:
        client.put('/api/mcp-settings', json={'public_url': '', 'enabled': True})


def test_keyless_upload_and_preview_download(client):
    data = io.BytesIO()
    Image.new('RGB', (25, 25), 'white').save(data, 'PNG')
    response = client.post('/mangaflow/files', params={'filename': '本机.png'}, content=data.getvalue())
    assert response.status_code == 200, response.text
    identifier = response.json()['asset_id']
    downloaded = client.get(f'/mangaflow/previews/{identifier}')
    assert downloaded.status_code == 200
    assert downloaded.content == data.getvalue()
    assert client.get(f'/mcp/previews/{identifier}').status_code == 401


def test_standard_protocol_errors_and_notifications(client):
    headers = {'Accept': 'application/json, text/event-stream'}
    cases = [('tools/call', {'name': 'missing_tool', 'arguments': {}}),
             ('tools/list', {'cursor': 'invalid'}), ('prompts/list', {'cursor': 'invalid'}),
             ('prompts/get', {'name': 'missing_prompt'})]
    for method, params in cases:
        response = client.post('/mangaflow', headers=headers, json={'jsonrpc': '2.0', 'id': 7, 'method': method, 'params': params})
        assert response.status_code == 200, response.text
        assert response.json()['id'] == 7
        assert response.json()['error']['code'] == -32602, response.text
    response = client.post('/mangaflow', headers=headers, json={'jsonrpc': '2.0', 'method': 'notifications/initialized'})
    assert response.status_code == 202 and not response.content
    response = client.post('/mangaflow', headers={**headers, 'MCP-Protocol-Version': '1900-01-01'},
        json={'jsonrpc': '2.0', 'id': 8, 'method': 'tools/list'})
    assert response.status_code == 400


def test_mutation_receipt_database_write_does_not_block_event_loop(client, monkeypatch):
    import asyncio

    from app.services import mcp_server
    original = mcp_server.finish_operation
    event_loop = []
    def observe(*args):
        try:
            asyncio.get_running_loop()
            event_loop.append(True)
        except RuntimeError:
            event_loop.append(False)
        return original(*args)
    monkeypatch.setattr(mcp_server, 'finish_operation', observe)
    project, _ = page(client)
    assert not rpc(client, 'workflow_start', mutation(project_id=project['id'])).get('isError')
    assert event_loop == [False]


def test_tool_rate_limit_is_shared_and_retries_do_not_duplicate_writes(client, monkeypatch):
    from app.main import app
    from app.services.mcp_server import ToolRateLimiter

    now = [0.0]
    monkeypatch.setattr(app.state, 'mcp_tool_limiter', ToolRateLimiter(rate=1, capacity=1, clock=lambda: now[0]))
    project, _ = page(client)
    arguments = mutation(project_id=project['id'])
    first = rpc(client, 'workflow_start', arguments)
    assert not first.get('isError'), first
    # Canonical /mangaflow and authenticated /mcp share the same allowance.
    limited = client.post('/mangaflow', headers={'Accept': 'application/json, text/event-stream'},
        json={'jsonrpc': '2.0', 'id': 2, 'method': 'tools/call',
              'params': {'name': 'workflow_start', 'arguments': arguments}}).json()['result']
    assert limited['isError']
    assert limited['structuredContent']['error'] == {
        'code': 'rate_limited', 'status': 429, 'retryable': True, 'retry_after_seconds': 1,
        'message': 'Too many MCP tool calls; wait and retry with the same operation_id.',
    }
    now[0] = 1.0
    retried = rpc(client, 'workflow_start', arguments)
    assert not retried.get('isError'), retried
    assert retried['structuredContent']['run_id'] == first['structuredContent']['run_id']
    now[0] = 2.0
    assert not rpc(client, 'workflow_get').get('isError')
















def test_stream_upload_is_visible_without_becoming_importable(client):
    import asyncio

    import httpx
    from app.main import app
    token=client.get('/api/mcp-settings').json()['token']
    headers={'Host':'localhost','Authorization':f'Bearer {token}','Content-Length':'2048'}
    async def check():
        resume, started=asyncio.Event(),asyncio.Event()
        async def chunks():
            yield b'a'*1024
            started.set()
            await resume.wait()
            yield b'b'*1024
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://localhost') as http:
            pending=asyncio.create_task(http.post('/mcp/files?filename=stream.cbz',headers=headers,content=chunks()))
            await asyncio.wait_for(started.wait(),5)
            rows=(await http.get('/api/mcp-settings/uploads')).json()
            row=next(row for row in rows if row['filename'] == 'stream.cbz')
            assert row['status'] == 'UPLOADING' and row['progress'] < 1
            from app.services.mcp_files import require_asset
            from fastapi import HTTPException
            with pytest.raises(HTTPException):
                require_asset(row['id'])
            resume.set()
            response=await asyncio.wait_for(pending,5)
            assert response.status_code == 200
            assert require_asset(response.json()['asset_id']).size == 2048
    asyncio.run(check())






def test_restart_receipt_and_expired_asset(client):
    from datetime import timedelta

    from app.core.database import SessionLocal
    from app.models import MCPAsset, MCPOperation
    from app.models.entities import utcnow
    from app.services.mcp_files import asset_path
    from app.services.mcp_server import recover_operations
    identifier = str(uuid4())
    with SessionLocal() as db:
        db.add(MCPOperation(id=identifier, fingerprint='a'*64, status='RUNNING', result={}))
        db.add(MCPAsset(id=identifier, filename='expired.png', size=1, created_at=utcnow()-timedelta(hours=25)))
        db.commit()
    asset_path(identifier).parent.mkdir(parents=True, exist_ok=True)
    asset_path(identifier).write_bytes(b'x')
    recover_operations()
    assert rpc(client, 'operation_status', {'operation_id': identifier})['structuredContent']['status'] == 'UNKNOWN'
    assert not asset_path(identifier).exists()


def test_real_http_python_and_node_sdk(monkeypatch):
    """Real socket, official Python/TS SDKs, and portable stdio bridge."""
    import asyncio
    import json
    import os
    import shutil
    import socket
    import subprocess
    import threading
    import time
    from pathlib import Path
    from types import SimpleNamespace
    from zipfile import ZipFile

    import httpx
    import uvicorn
    from app.main import app
    from app.pipeline.processor import PipelineProcessor
    from app.services import mcp_workflows
    from app.services.base import DetectionResult, OCRResult
    from app.services.translation.providers import OpenAICompatibleTranslator
    from mcp import ClientSession
    from mcp.client.streamable_http import streamablehttp_client

    class Translator(OpenAICompatibleTranslator):
        async def translate_regions(self, regions, **kwargs):
            return {identifier: "Hello" for identifier, _ in regions}

    def inpaint(image_path, mask_path, output_path, region_type):
        with Image.open(image_path) as original:
            Image.new("RGB", original.size, "white").save(output_path)
        return output_path

    providers = {
        "detection": SimpleNamespace(detect=lambda path: [DetectionResult(bbox=[20, 20, 120, 100],
            polygon=[[20, 20], [140, 20], [140, 120], [20, 120]], confidence=.99, orientation="horizontal")]),
        "ocr": SimpleNamespace(ensure_loaded=lambda: None, recognize=lambda *args, **kwargs: OCRResult(
            text="こんにちは", confidence=.99, orientation="horizontal")),
        "translation": Translator({}), "inpainting": SimpleNamespace(inpaint=inpaint),
    }
    original_provider = PipelineProcessor._provider
    monkeypatch.setattr(PipelineProcessor, "_provider", lambda processor, kind, requested:
        (providers[kind], "native-protocol-test") if kind in providers else original_provider(processor, kind, requested))
    monkeypatch.setattr(mcp_workflows, "blockers", lambda db, run, **kwargs: [])

    sock = socket.socket()
    sock.bind(('127.0.0.1', 0))
    port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, log_level='error'))
    thread = threading.Thread(target=server.run, kwargs={'sockets': [sock]}, daemon=True)
    thread.start()
    base = f'http://127.0.0.1:{port}'
    try:
        for _ in range(200):
            if server.started:
                break
            time.sleep(.05)
        assert server.started
        with httpx.Client(base_url=base) as live_client:
            _, image = page(live_client)
        picture = io.BytesIO()
        Image.new('RGB', (20,20), 'white').save(picture, 'PNG')
        comic_picture = io.BytesIO()
        Image.new('RGB', (300, 200), 'white').save(comic_picture, 'PNG')
        comic = io.BytesIO()
        with ZipFile(comic, 'w') as archive:
            for number in (10, 2):
                archive.writestr(f'漫画/{number}.png', comic_picture.getvalue())
        async def check():
            async with streamablehttp_client(base+'/mangaflow') as (reader, writer, _):
                async with ClientSession(reader, writer) as session:
                    initialized = await session.initialize()
                    assert initialized.serverInfo.name == 'MangaFlow'
                    tools = await session.list_tools()
                    assert len(tools.tools) == 20
                    guide = await session.call_tool('workflow_get', {})
                    assert guide.structuredContent['instructions']
                    prompts = await session.list_prompts()
                    assert prompts.prompts[0].name == 'translate_and_review'
                    observed = await session.call_tool('page_state', {'image_id': image['id']})
                    started = await session.call_tool('workflow_start', {'project_id': observed.structuredContent['page']['project_id'], 'operation_id': str(uuid4())})
                    queued = await session.call_tool('workflow_advance', {
                        'run_id': started.structuredContent['run_id'], 'image_id': image['id'], 'operation_id': str(uuid4()),
                        'expected_versions': observed.structuredContent['expected_versions'],
                    })
                    assert not queued.isError, queued
                    finished = await session.call_tool('task_wait', {
                        'task_id': queued.structuredContent['task']['id'], 'kind': 'processing', 'seconds': 20,
                    })
                    assert finished.structuredContent['status'] == 'COMPLETED', finished
                    preview = await session.call_tool('page_preview', {'image_id':image['id']})
                    assert preview.content[1].type == 'image'
        asyncio.run(check())
        root = Path(__file__).resolve().parents[2]
        script = root/'connectors/mangaflow-mcp/smoke.mjs'
        node = shutil.which('node') or shutil.which('node.exe')
        if node and (script.parent/'node_modules').exists():
            script_name = str(script)
            if node.endswith('.exe') and os.name != 'nt':
                script_name = subprocess.check_output(['wslpath', '-w', script_name], text=True).strip()
            import base64
            completed = subprocess.run([node, script_name], input=json.dumps({'url': base+'/mangaflow',
                'image_id':image['id'],'image_data':base64.b64encode(picture.getvalue()).decode(),
                'comic_data':base64.b64encode(comic.getvalue()).decode(),
                'page_data':base64.b64encode(comic_picture.getvalue()).decode()}),
                text=True, capture_output=True, timeout=240)
            assert completed.returncode == 0, completed.stderr
            assert 'stdio: initialize' in completed.stdout
            assert 'stdio: native comic import/detect/ocr/translate/mask/inpaint/render/review/export/download passed' in completed.stdout
            assert 'stdio: directory 11-page auto-create/import/translate/review/export passed' in completed.stdout
            for mode in ('http', 'stdio'):
                assert f'{mode}: independent workers, exclusive claim, native image, receipt and coordinator guard passed' in completed.stdout
                assert f'{mode}: configuration change, same-run recovery and renewed review passed' in completed.stdout
    finally:
        server.should_exit = True
        thread.join(15)
        sock.close()
