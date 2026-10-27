"""Versioned MCP commands, geometric invariants and bounded state responses."""
import json

import pytest
from app.core.database import SessionLocal
from app.models import ProcessingTask, TextRegion
from app.models.entities import utcnow
from sqlalchemy import select
from test_mcp import page, rpc


@pytest.fixture(autouse=True)
def local_host(client):
    client.headers['Host'] = 'localhost'


def sample(client):
    project, image = page(client)
    response = client.post(f"/api/images/{image['id']}/regions", json={
        'bbox': [20, 20, 100, 100], 'source_text': 'Initial', 'translated_text': 'English', 'orientation': 'horizontal'})
    assert response.status_code == 201, response.text
    return project, image, response.json()


def state(client, image):
    return rpc(client, 'page_state', {'image_id': image['id']})['structuredContent']


def publish_unseen(region, text='Unseen newer OCR result'):
    with SessionLocal() as db:
        row = db.get(TextRegion, region['id'])
        row.source_text, row.updated_at = text, utcnow()
        db.commit()


def stop_tasks(project_id):
    with SessionLocal() as db:
        for row in db.scalars(select(ProcessingTask).where(ProcessingTask.project_id == project_id)):
            row.status = 'CANCELLED'
        db.commit()




















def test_compact_state_preserves_editable_fields_and_paginates_details(client):
    _, image, region = sample(client)
    second = client.post(f"/api/images/{image['id']}/regions", json={'bbox': [150, 20, 40, 40], 'reading_order': -1}).json()
    with SessionLocal() as db:
        row = db.get(TextRegion, region['id'])
        row.layout_data = {'manual': True, 'auto_font_size': False, 'placements': ['x' * 20000] * 10,
                           'mask_generation': {'mask_integrity': {'status': 'partial', 'glyph_safe_retention': .7, 'raw': 'x' * 20000}},
                           'repair_quality': {'status': 'uncertain', 'raw_response': 'x' * 20000}}
        db.commit()
    compact = state(client, image)
    assert len(json.dumps(compact)) < 10000
    assert compact['detail_level'] == 'summary' and compact['regions'][0]['id'] == second['id']
    assert compact['regions'][1]['state']['repair_quality'] == {'status': 'uncertain'}
    assert compact['regions'][1]['state']['mask_generation']['mask_integrity'] == {'status': 'partial', 'glyph_safe_retention': .7}
    first_page = rpc(client, 'page_state', {'image_id': image['id'], 'limit': 1})['structuredContent']
    assert first_page['next_offset'] == 1 and first_page['matched_region_count'] == 2
    next_page = rpc(client, 'page_state', {'image_id': image['id'], 'limit': 1, 'offset': 1, 'expected_version': first_page['version']})['structuredContent']
    assert next_page['next_offset'] is None and next_page['regions'][0]['id'] == region['id']
    details = rpc(client, 'page_state', {'image_id': image['id'], 'region_ids': [region['id']], 'include_details': True})['structuredContent']
    assert details['detail_level'] == 'full' and len(details['regions']) == 1
    assert 'placements' not in details['regions'][0].get('state', {})
    assert 'font_size' in details['regions'][0]['style']
    assert details['version'] == compact['version']


def test_state_pagination_refuses_mixed_versions(client):
    _, image, region = sample(client)
    current = state(client, image)
    missing = rpc(client, 'page_state', {'image_id': image['id'], 'offset': 1})
    assert missing['isError'] and missing['structuredContent']['error']['status'] == 409
    publish_unseen(region)
    stale = rpc(client, 'page_state', {'image_id': image['id'], 'offset': 1, 'expected_version': current['version']})
    assert stale['isError'] and stale['structuredContent']['error']['status'] == 409


def test_state_rejects_a_publication_during_serialization(client, monkeypatch):
    from app.services import mcp_tools
    _, image, region = sample(client)
    original = mcp_tools.current_page_version

    def raced(*args, **kwargs):
        result = original(*args, **kwargs)
        publish_unseen(region)
        return result

    monkeypatch.setattr(mcp_tools, 'current_page_version', raced)
    result = rpc(client, 'page_state', {'image_id': image['id']})
    assert result['isError'] and result['structuredContent']['error']['status'] == 409
