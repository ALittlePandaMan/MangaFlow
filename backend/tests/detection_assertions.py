"""Shared integration assertions for whole-page frame replacement."""


def assert_rebuilt_regions(regions, detections, previous_ids):
    expected = [r for r in detections if not r.metadata.get('text_classification', {}).get('excluded', False)]
    assert not previous_ids & {r.id for r in regions}
    assert [r.bbox for r in regions] == [r.bbox for r in expected]
    assert [r.polygon for r in regions] == [r.polygon for r in expected]
    assert [r.orientation for r in regions] == [r.orientation for r in expected]
    assert all(not r.locked and r.source_text == '' and r.translated_text == '' for r in regions)
    assert all(not (r.layout_data or {}).get('manual') for r in regions)
