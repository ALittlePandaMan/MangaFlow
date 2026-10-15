"""Small tensor regressions; no checkpoints, network, CUDA or live page writes."""
from unittest.mock import patch

import numpy as np
import pytest
from PIL import Image

torch = pytest.importorskip("torch")
from app.services.detection.layout_inference import postprocess_layout, prepare_image  # noqa: E402


def outputs():
    return {"pred_logits": torch.full((1, 300, 5), -20.),
            "pred_boxes": torch.full((1, 300, 4), .4),
            "pred_masks": torch.zeros((1, 300, 4, 4))}


def test_empty_high_resolution_page_does_not_allocate_any_full_page_masks():
    with patch("app.services.detection.layout_inference.F.interpolate") as resize:
        assert postprocess_layout(outputs(), 4000, 6000) == []
        resize.assert_not_called()


def test_thresholds_and_background_are_filtered_before_single_mask_resize():
    values = outputs()
    for query, label, confidence in [(0, 0, .9), (1, 2, .8), (2, 1, .3), (3, 3, .4), (4, 4, .99)]:
        values["pred_logits"][0, query, label] = torch.logit(torch.tensor(confidence))
        values["pred_masks"][0, query, 1:3, 1:3] = 2
        values["pred_masks"][0, query, 0, :] = -2
    interpolate = torch.nn.functional.interpolate
    with patch("app.services.detection.layout_inference.F.interpolate", wraps=interpolate) as resize:
        result = postprocess_layout(values, 80, 100)
    assert [item.label for item in result] == ["text", "bubble", "onomatopoeia"]
    assert len(resize.call_args_list) == 3
    assert all(call.args[0].shape == (1, 1, 4, 4) for call in resize.call_args_list)
    for item, query in zip(result, [0, 1, 2], strict=True):
        expected = interpolate(values["pred_masks"][:, query:query+1], size=(100, 80),
                               mode="bilinear", align_corners=False)[0, 0].numpy() > 0
        actual = np.zeros_like(expected)
        x, y = item.mask_origin
        h, w = item.mask.shape
        actual[y:y+h, x:x+w] = item.mask > 0
        np.testing.assert_array_equal(actual, expected)
        assert item.mask.base is None  # crop must not retain a full-page tensor
    with patch("app.services.detection.layout_inference.F.interpolate", wraps=interpolate) as resize:
        assert len(postprocess_layout(values, 80, 100, (.95, .4, .9, .5))) == 0
        resize.assert_not_called()


def test_preprocessing_matches_koharu_bilinear_resize_and_normalization():
    rgb = np.zeros((8, 8, 3), np.uint8)
    rgb[::2, ::2] = 255
    result = prepare_image(Image.fromarray(rgb), "cpu", resolution=4)
    raw = torch.from_numpy(rgb).permute(2, 0, 1).unsqueeze(0).float()/255
    expected = torch.nn.functional.interpolate(raw, (4, 4), mode="bilinear", align_corners=False)
    expected = (expected-torch.tensor([.485, .456, .406])[None, :, None, None])/torch.tensor([.229, .224, .225])[None, :, None, None]
    torch.testing.assert_close(result, expected)


def test_invalid_thresholds_fail_before_any_mask_allocation():
    with patch("app.services.detection.layout_inference.F.interpolate") as resize:
        with pytest.raises(ValueError, match="thresholds"):
            postprocess_layout(outputs(), 80, 100, (.25, float("nan"), .5, .5))
        resize.assert_not_called()
