import asyncio
import json
from uuid import uuid4

import numpy as np
import pytest
from app.services.base import ProviderError, ProviderState
from app.services.cloud_budget import RequestBudget
from app.services.inpainting.providers import SimpleLaMaInpainter
from app.services.translation.providers import OpenAICompatibleTranslator
from PIL import Image
from test_editor_history import apply
from test_repair_geometry_sync import setup_page


def test_client_generated_region_id_is_durable_unique_and_reversible(client):
    page, _ = setup_page(client)
    id = str(uuid4())
    body = {"id": id, "bbox": [10, 10, 20, 50]}
    url = f"/api/images/{page['id']}/regions"
    created = client.post(url, json=body)
    assert created.status_code == 201, created.text
    assert created.json()["id"] == id
    assert client.post(url, json=body).status_code == 409
    assert client.post(url, json={**body, "id": "invalid"}).status_code == 422
    assert id not in {region["id"] for region in apply(client, page["project_id"])["regions"]}
    assert id in {region["id"] for region in apply(client, page["project_id"], "redo")["regions"]}


def test_bulk_geometry_and_alignment_are_one_reversible_history_step(client):
    page, regions = setup_page(client, 2)
    changes = [
        {
            "id": region["id"],
            "patch": {
                "translated_bbox": [20, 40 + i * 80, 40, 60],
                "translated_polygon": [[20, 40 + i * 80], [60, 40 + i * 80], [60, 100 + i * 80], [20, 100 + i * 80]],
                "alignment": "left",
            },
        }
        for i, region in enumerate(regions)
    ]
    response = client.post("/api/regions/bulk-update", json=changes)
    assert response.status_code == 200, response.text
    assert len({region["editor_history"]["undo_id"] for region in response.json()}) == 1
    before = {region["id"]: region for region in regions}
    undone = apply(client, page["project_id"])["regions"]
    for region in undone:
        assert region["translated_bbox"] == before[region["id"]]["translated_bbox"]
        assert region["alignment"] == before[region["id"]]["alignment"]
        assert region["bbox"] == before[region["id"]]["bbox"]
    redone = apply(client, page["project_id"], "redo")["regions"]
    assert all(region["alignment"] == "left" and region["translated_bbox"][0] == 20 for region in redone)


@pytest.mark.parametrize("position", [(30, 30), (1300, 1800)])
def test_lama_local_repair_crops_native_resolution_preserves_outside_and_metadata(tmp_path, position):
    image = Image.new("RGB", (1600, 2400), (10, 30, 50))
    x, y = position
    mask = np.zeros((2400, 1600), np.uint8)
    mask[y : y + 80, x : x + 50] = 255
    source, mask_path, output = [tmp_path / name for name in ("source.png", "mask.png", "output.png")]
    image.save(source, dpi=(300, 300))
    Image.fromarray(mask).save(mask_path)
    calls = []
    provider = SimpleLaMaInpainter()
    provider.state = ProviderState.READY

    def model(crop, crop_mask):
        calls.append((crop.size, crop_mask.getbbox()))
        return Image.new("RGB", (crop.width + 7, crop.height + 7), (210, 220, 230))

    provider._model = model
    provider.inpaint(source, mask_path, output, "background_complex")
    assert calls[0][0] == (512, 512)
    with Image.open(output) as result:
        assert result.size == image.size and result.info["dpi"][0] == pytest.approx(300, abs=0.02)
        pixels = np.asarray(result)
    assert np.array_equal(pixels[mask == 0], np.asarray(image)[mask == 0])
    assert np.all(pixels[mask > 0] == (210, 220, 230))


def test_lama_empty_mask_never_loads_model_and_crop_opt_out_uses_whole_image(tmp_path):
    source, mask, output = [tmp_path / name for name in ("source.png", "mask.png", "output.png")]
    Image.new("RGB", (900, 1000), "white").save(source)
    Image.new("L", (900, 1000), 0).save(mask)
    provider = SimpleLaMaInpainter()
    provider.inpaint(source, mask, output, "background_complex")
    assert provider.state == ProviderState.UNLOADED
    pixels = np.zeros((1000, 900), np.uint8)
    pixels[30:50, 30:50] = 255
    Image.fromarray(pixels).save(mask)
    provider = SimpleLaMaInpainter({"crop_context": False})
    provider.state = ProviderState.READY
    provider._model = lambda image, _mask: image if image.size == (900, 1000) else pytest.fail("unexpected crop")
    provider.inpaint(source, mask, output, "background_complex")


@pytest.mark.asyncio
async def test_dense_translation_uses_one_connection_pool_two_bounded_workers_and_same_context(monkeypatch):
    import app.services.translation.providers as module
    import httpx

    real_client = httpx.AsyncClient
    active = 0
    maximum = 0
    calls = []
    clients = []

    async def reply(request):
        nonlocal active, maximum
        payload = json.loads(request.content)
        instructions = json.loads(payload["messages"][1]["content"].split("\n\n")[0])
        ids = list(instructions["required_output"])
        calls.append(instructions)
        active += 1
        maximum = max(maximum, active)
        await asyncio.sleep(0.03)
        active -= 1
        return httpx.Response(
            200, json={"choices": [{"message": {"content": json.dumps({key: "translated " + key for key in ids})}}]}
        )

    def client(**kwargs):
        value = real_client(transport=httpx.MockTransport(reply), **kwargs)
        clients.append(value)
        return value

    monkeypatch.setattr(module.httpx, "AsyncClient", client)
    budget = RequestBudget()
    monkeypatch.setattr(module, "request_budget", lambda _config: budget)
    provider = OpenAICompatibleTranslator(
        {
            "base_url": "https://example.test/v1",
            "requests_per_minute": 6000,
            "concurrency_mode": "fixed",
            "max_concurrency": 2,
        }
    )
    regions = [(f"R{i:03d}", "source") for i in range(75)]
    result = await provider.translate_regions(
        regions, source_language="ja", target_language="zh", context={"glossary": {"name": "名字"}}
    )
    assert list(result) == [key for key, _ in regions]
    assert len(calls) == 3 and len(clients) == 1 and maximum == 2
    assert all(call["project_context"] == {"glossary": {"name": "名字"}} for call in calls)
    assert all(len(call["required_output"]) <= 32 for call in calls)
    assert budget.active == 0


@pytest.mark.asyncio
async def test_translation_retries_only_missing_and_empty_entries(monkeypatch):
    import app.services.translation.providers as module
    import httpx

    real_client = httpx.AsyncClient
    requests = []

    async def reply(request):
        instructions = json.loads(json.loads(request.content)["messages"][1]["content"].split("\n\n")[0])
        requests.append(instructions["required_output"])
        result = (
            {"R001": "keep first", "R002": ""} if len(requests) == 1 else {"R002": "new second", "R003": "new third"}
        )
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(result)}}]})

    monkeypatch.setattr(
        module.httpx, "AsyncClient", lambda **kwargs: real_client(transport=httpx.MockTransport(reply), **kwargs)
    )
    monkeypatch.setattr(module, "request_budget", lambda _config: budget)
    budget = RequestBudget()
    provider = OpenAICompatibleTranslator({"base_url": "https://missing.test/v1", "requests_per_minute": 6000})
    result = await provider.translate_regions(
        [("R001", "one"), ("R002", "two"), ("R003", "three")], source_language="ja", target_language="zh", context={}
    )
    assert result == {"R001": "keep first", "R002": "new second", "R003": "new third"}
    assert set(requests[1]) == {"R002", "R003"}


def test_translation_parser_rejects_null_or_numeric_values():
    with pytest.raises(ProviderError):
        OpenAICompatibleTranslator._parse('{"R001":null}')
