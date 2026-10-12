import hashlib
from pathlib import Path

import cv2
import numpy as np
from app.services.inpainting.masking import MASK_ALGORITHM_VERSION, create_text_mask, create_text_mask_union
from app.services.inpainting.repair import repair_masks
from PIL import Image, ImageCms


def test_neighbouring_ink_is_not_a_background_colour(tmp_path):
    image = np.full((280, 300, 3), 255, np.uint8)
    polygon = [[100, 60], [160, 60], [160, 220], [100, 220]]
    glyphs = np.zeros(image.shape[:2], np.uint8)
    for y in range(75, 210, 20):
        cv2.rectangle(glyphs, (115, y), (145, y + 4), 255, -1)
    image[glyphs > 0] = 0
    cv2.rectangle(image, (94, 70), (97, 210), (0, 0, 0), -1)
    cv2.rectangle(image, (163, 70), (166, 210), (0, 0, 0), -1)
    path = tmp_path / "mask.png"
    create_text_mask(Path("unused"), polygon, path, source_image=image, conservative=True)
    mask = cv2.imread(str(path), 0)
    assert np.all(mask[glyphs > 0] == 255)
    assert np.all(mask[:, :100] == 0)  # No untrusted outward growth.


def test_balloon_search_recovers_clipped_stroke_but_not_unrelated_ink(tmp_path):
    image = np.full((240, 300, 3), 250, np.uint8)
    balloon = np.zeros(image.shape[:2], np.uint8)
    cv2.ellipse(balloon, (150, 120), (130, 105), 0, 0, 360, 255, -1)
    cv2.rectangle(image, (97, 90), (117, 104), (0, 0, 0), -1)
    image[94:101, 95:97] = 225  # Antialias fringe outside the OCR box.
    image[150:153, 95:97] = 0  # Detached illustration just outside the box.
    path = tmp_path / "mask.png"
    meta = create_text_mask(
        Path("unused"),
        [[100, 60], [170, 60], [170, 180], [100, 180]],
        path,
        source_image=image,
        balloon_mask=balloon,
        expand=2,
    )
    mask = cv2.imread(str(path), 0)
    assert np.all(mask[90:105, 97:118] == 255)
    assert np.all(mask[94:101, 95:97] == 255)
    assert np.all(mask[150:153, 95:97] == 0)
    assert np.all(mask[balloon == 0] == 0)
    assert meta["method"] == "balloon_glyph"


def test_joint_lines_share_background_and_keep_the_gap(tmp_path):
    image = np.full((240, 300, 3), 245, np.uint8)
    balloon = np.zeros(image.shape[:2], np.uint8)
    cv2.ellipse(balloon, (150, 120), (130, 105), 0, 0, 360, 255, -1)
    polygons = [[[90, 55], [130, 55], [130, 190], [90, 190]], [[145, 55], [185, 55], [185, 190], [145, 190]]]
    for x in (108, 156):
        for y in range(70, 180, 20):
            image[y : y + 6, x : x + 13] = 0
    path = tmp_path / "mask.png"
    meta = create_text_mask_union(Path("unused"), polygons, path, source_image=image, balloon_mask=balloon)
    mask = cv2.imread(str(path), 0)
    assert np.all(mask[image[:, :, 0] == 0] == 255)
    assert np.all(mask[60:190, 137] == 0)
    assert meta["source_count"] == 2
    assert meta["repair"]["strategy"] == "uniform_fill"


def test_real_outline_is_protected_even_when_model_mask_is_too_large(tmp_path):
    image = np.full((300, 240, 3), 240, np.uint8)
    outline = np.zeros(image.shape[:2], np.uint8)
    cv2.ellipse(outline, (120, 150), (72, 118), 0, 0, 360, 255, 4)
    image[outline > 0] = 0
    image[100:108, 100:140] = 0
    image[140:148, 100:140] = 0
    balloon = np.zeros(image.shape[:2], np.uint8)
    cv2.ellipse(balloon, (120, 150), (80, 126), 0, 0, 360, 255, -1)
    path = tmp_path / "mask.png"
    meta = create_text_mask(
        Path("unused"),
        [[30, 15], [210, 15], [210, 285], [30, 285]],
        path,
        source_image=image,
        balloon_mask=balloon,
    )
    mask = cv2.imread(str(path), 0)
    assert np.all(mask[outline > 0] == 0)
    assert np.all(mask[100:108, 100:140] == 255)
    assert np.all(mask[140:148, 100:140] == 255)
    assert meta["constraint"]["outline_protected_pixels"] > 0


def test_gradient_and_screen_tone_are_not_authorized_for_flat_fill(tmp_path):
    for kind in ("gradient", "screen-tone"):
        image = np.full((200, 260, 3), 240, np.uint8)
        if kind == "gradient":
            image[:] = np.linspace(200, 255, 260).astype(np.uint8)[None, :, None]
        else:
            image[::3, ::3] = 170
        image[80:90, 115:140] = 0
        balloon = np.zeros(image.shape[:2], np.uint8)
        cv2.ellipse(balloon, (130, 100), (110, 85), 0, 0, 360, 255, -1)
        meta = create_text_mask(
            Path("unused"),
            [[105, 65], [150, 65], [150, 110], [105, 110]],
            tmp_path / f"{kind}.png",
            source_image=image,
            balloon_mask=balloon,
        )
        assert "repair" not in meta


def test_paper_sampling_ignores_verified_panel_shadow_not_screentone(tmp_path):
    from app.services.inpainting.background import paper_background

    image = np.full((100, 240, 3), 255, np.uint8)
    region = np.zeros(image.shape[:2], np.uint8)
    region[40:64, 30:210] = 255
    # A panel rule and its narrow shadow are just above the source text.
    image[34:36, :] = 0
    image[36:39, :] = 150
    for x in range(40, 200, 15):
        image[46:58, x:x+4] = 0
    ring = cv2.subtract(cv2.dilate(region, np.ones((15, 15), np.uint8)), region)
    lab = cv2.cvtColor(image, cv2.COLOR_BGR2LAB).astype(np.float32)
    evidence = paper_background(image, lab, ring, region)
    assert evidence and evidence["uniform"]
    assert evidence["structure_excluded_samples"] > 0
    image[::3, ::3] = 170
    lab = cv2.cvtColor(image, cv2.COLOR_BGR2LAB).astype(np.float32)
    assert paper_background(image, lab, ring, region) is None


class RecordingRepair:
    def __init__(self):
        self.masks = []

    def inpaint(self, source, mask_path, output, _kind):
        mask = cv2.imread(str(mask_path), 0)
        self.masks.append(mask)
        # Deliberately changes outside pixels too: routing must composite safely.
        with Image.open(source) as image:
            Image.new("RGB", image.size, (80, 90, 100)).save(output)
        return output


def fill_metadata(mask, color):
    return {
        "version": MASK_ALGORITHM_VERSION,
        "method": "balloon_glyph",
        "constraint": {"status": "applied"},
        "background": {"uniform": True},
        "repair": {
            "strategy": "uniform_fill",
            "color_rgb": color,
            "mask_sha256": hashlib.sha256(mask.tobytes()).hexdigest(),
        },
    }


def test_flat_fill_has_no_black_edge_and_preserves_unmasked_pixels_and_icc(tmp_path):
    source = tmp_path / "source.png"
    image = np.full((80, 120, 3), 247, np.uint8)
    image[20:60, 40:60] = 0
    profile = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()
    Image.fromarray(image).save(source, icc_profile=profile)
    mask = np.zeros(image.shape[:2], np.uint8)
    mask[20:60, 40:60] = 255
    path = tmp_path / "mask.png"
    cv2.imwrite(str(path), mask)
    provider = RecordingRepair()
    output = tmp_path / "output.png"
    repair_masks(provider, source, path, output, [(path, fill_metadata(mask, [247, 247, 247]))])
    with Image.open(output) as result:
        assert result.info["icc_profile"] == profile
        values = np.asarray(result)
        assert np.all(values[mask > 0] == 247)
        assert np.array_equal(values[mask == 0], image[mask == 0])
    assert not provider.masks


def test_mixed_repairs_send_only_complex_pixels_to_model(tmp_path):
    source = tmp_path / "source.png"
    Image.new("RGB", (120, 80), (240, 230, 220)).save(source)
    flat = np.zeros((80, 120), np.uint8)
    flat[10:60, 20:60] = 255
    complex_mask = np.zeros_like(flat)
    complex_mask[40:70, 50:100] = 255  # Overlap belongs to the complex repair.
    combined = cv2.bitwise_or(flat, complex_mask)
    paths = [tmp_path / name for name in ("flat.png", "complex.png", "union.png")]
    for p, mask in zip(paths, (flat, complex_mask, combined), strict=True):
        cv2.imwrite(str(p), mask)
    provider = RecordingRepair()
    output = tmp_path / "output.png"
    repair_masks(provider, source, paths[2], output, [(paths[0], fill_metadata(flat, [250, 250, 250])), (paths[1], {})])
    result = np.asarray(Image.open(output))
    assert len(provider.masks) == 1
    assert np.array_equal(provider.masks[0], complex_mask)
    assert np.all(result[(flat > 0) & (complex_mask == 0)] == 250)
    assert np.all(result[complex_mask > 0] == [80, 90, 100])
    assert np.all(result[combined == 0] == [240, 230, 220])
    assert not list(tmp_path.glob(".*.png"))


def test_edited_mask_cannot_reuse_old_flat_fill_decision(tmp_path):
    source = tmp_path / "source.png"
    Image.new("RGB", (120, 80), "white").save(source)
    mask = np.zeros((80, 120), np.uint8)
    mask[20:40, 20:40] = 255
    meta = fill_metadata(mask, [255, 255, 255])
    mask[60:70, 60:70] = 255
    path = tmp_path / "mask.png"
    cv2.imwrite(str(path), mask)
    provider = RecordingRepair()
    repair_masks(provider, source, path, tmp_path / "out.png", [(path, meta)])
    assert len(provider.masks) == 1
    assert np.array_equal(provider.masks[0], mask)


def test_dense_paper_text_fills_without_model_and_preserves_crossing_rules(tmp_path):
    image = np.full((180, 300, 3), 255, np.uint8)
    # A T-junction must not stop recognition of either straight panel rule.
    image[99:102, 5:280] = 0
    image[5:150, 230:233] = 0
    glyphs = np.zeros(image.shape[:2], np.uint8)
    for x in range(55, 200, 17):
        glyphs[70:88, x : x + 6] = 255
        glyphs[75:81, x : x + 11] = 255
    glyphs[86:99, 150:154] = 255  # A letter touches the rule, but is not the rule.
    image[glyphs > 0] = 35
    source = tmp_path / "source.png"
    Image.fromarray(image).save(source)
    path = tmp_path / "mask.png"
    meta = create_text_mask(source, [[40, 60], [240, 60], [240, 107], [40, 107]], path, conservative=True)
    mask = cv2.imread(str(path), 0)
    assert np.all(mask[99:102] == 0)
    assert np.all(mask[:, 230:233] == 0)
    safe_glyphs = (glyphs > 0) & (np.indices(glyphs.shape)[0] < 98)
    assert np.all(mask[safe_glyphs] == 255)
    assert meta["background"]["scope"] == "local_paper"
    provider = RecordingRepair()
    output = tmp_path / "clean.png"
    repair_masks(provider, source, path, output, [(path, meta)])
    result = np.asarray(Image.open(output))
    assert np.all(result[safe_glyphs] == 255)
    assert np.array_equal(result[mask == 0], image[mask == 0])
    assert not provider.masks


def test_paper_inference_rejects_texture_and_gradient(tmp_path):
    for kind in ("dots", "gradient", "colour"):
        image = np.full((140, 220, 3), 255, np.uint8)
        if kind == "dots":
            image[::4, ::4] = 30
        elif kind == "gradient":
            image[:] = np.linspace(215, 255, 220).astype(np.uint8)[None, :, None]
        else:
            image[:] = [230, 245, 255]
        image[55:85, 100:108] = 0
        meta = create_text_mask(
            Path("unused"),
            [[75, 35], [135, 35], [135, 105], [75, 105]],
            tmp_path / f"{kind}.png",
            source_image=image,
            conservative=True,
        )
        assert "repair" not in meta


def test_empty_balloon_does_not_become_a_full_rectangle(tmp_path):
    image = np.full((100, 140, 3), 255, np.uint8)
    balloon = np.zeros(image.shape[:2], np.uint8)
    cv2.ellipse(balloon, (70, 50), (60, 45), 0, 0, 360, 255, -1)
    path = tmp_path / "empty.png"
    create_text_mask(
        Path("unused"), [[40, 20], [90, 20], [90, 80], [40, 80]], path, source_image=image, balloon_mask=balloon
    )
    assert not np.any(cv2.imread(str(path), 0))


def test_white_letters_in_black_balloon(tmp_path):
    image = np.zeros((140, 180, 3), np.uint8)
    image[50:90, 80:90] = 255
    balloon = np.zeros(image.shape[:2], np.uint8)
    cv2.ellipse(balloon, (90, 70), (80, 65), 0, 0, 360, 255, -1)
    path = tmp_path / "black.png"
    meta = create_text_mask(
        Path("unused"), [[65, 35], [110, 35], [110, 105], [65, 105]], path, source_image=image, balloon_mask=balloon
    )
    mask = cv2.imread(str(path), 0)
    assert np.all(mask[50:90, 80:90] == 255)
    assert meta["repair"]["color_rgb"] == [0, 0, 0]


def test_conflicting_flat_colours_route_overlap_to_model_in_either_order(tmp_path):
    source = tmp_path / "source.png"
    Image.new("RGB", (100, 60), "white").save(source)
    left = np.zeros((60, 100), np.uint8)
    right = np.zeros_like(left)
    left[20:40, 10:60] = 255
    right[20:40, 40:90] = 255
    paths = [tmp_path / name for name in ("left.png", "right.png", "combined.png")]
    for path, mask in zip(paths, (left, right, left | right), strict=True):
        cv2.imwrite(str(path), mask)
    regions = [(paths[0], fill_metadata(left, [250, 250, 250])), (paths[1], fill_metadata(right, [235, 235, 235]))]
    results = []
    for order in (regions, list(reversed(regions))):
        provider = RecordingRepair()
        output = tmp_path / "result.png"
        repair_masks(provider, source, paths[2], output, order)
        assert np.array_equal(provider.masks[0], left & right)
        results.append(np.asarray(Image.open(output)).copy())
    assert np.array_equal(*results)
