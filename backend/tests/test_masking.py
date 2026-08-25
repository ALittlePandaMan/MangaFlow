from pathlib import Path

import cv2
import numpy as np
from app.services.base import ProviderState
from app.services.inpainting import masking as masking_module
from app.services.inpainting.masking import (
    apply_balloon_constraint,
    create_region_mask,
    create_text_mask,
    create_text_mask_union,
    load_text_mask_source,
    mask_is_empty,
    process_mask,
)
from app.services.inpainting.providers import HybridInpainter, OpenCVInpainter, SimpleLaMaInpainter
from PIL import Image, ImageCms


def _srgb_profile() -> bytes:
    return ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()


def test_mask_generation_and_morphology(tmp_path: Path) -> None:
    path = tmp_path / "mask.png"
    create_region_mask(100, 100, [[25, 25], [75, 25], [75, 75], [25, 75]], path, expand=0)
    original = cv2.countNonZero(cv2.imread(str(path), cv2.IMREAD_GRAYSCALE))
    process_mask(path, path, "dilate", 5)
    dilated = cv2.countNonZero(cv2.imread(str(path), cv2.IMREAD_GRAYSCALE))
    process_mask(path, path, "erode", 5)
    eroded = cv2.countNonZero(cv2.imread(str(path), cv2.IMREAD_GRAYSCALE))
    assert dilated > original
    assert eroded < dilated
    assert not mask_is_empty(path)
    process_mask(path, path, "clear", 3)
    assert mask_is_empty(path)


def test_text_mask_fills_tight_polygon_on_uniform_background(tmp_path: Path) -> None:
    image_path = tmp_path / "page.png"
    mask_path = tmp_path / "text-mask.png"
    image = np.full((100, 100, 3), 235, dtype=np.uint8)
    cv2.rectangle(image, (38, 25), (43, 75), (20, 20, 20), -1)
    cv2.rectangle(image, (55, 25), (60, 75), (20, 20, 20), -1)
    cv2.imwrite(str(image_path), image)
    metadata = create_text_mask(
        image_path,
        [[25, 15], [75, 15], [75, 85], [25, 85]],
        mask_path,
        expand=1,
    )
    mask = cv2.imread(str(mask_path), cv2.IMREAD_GRAYSCALE)
    masked = cv2.countNonZero(mask)
    assert 3000 < masked < 4000
    assert mask[50, 40] == 255
    assert mask[50, 30] == 255
    assert metadata["method"] == "uniform_background_polygon"
    assert metadata["suggested_region_type"] == "background_complex"


def test_conservative_text_mask_never_fills_uniform_ocr_rectangle(tmp_path: Path) -> None:
    image_path = tmp_path / "page.png"
    mask_path = tmp_path / "text-mask.png"
    image = np.full((100, 100, 3), 235, dtype=np.uint8)
    cv2.rectangle(image, (38, 25), (43, 75), (20, 20, 20), -1)
    cv2.rectangle(image, (55, 25), (60, 75), (20, 20, 20), -1)
    cv2.imwrite(str(image_path), image)

    metadata = create_text_mask(
        image_path,
        [[25, 15], [75, 15], [75, 85], [25, 85]],
        mask_path,
        expand=1,
        conservative=True,
    )
    mask = cv2.imread(str(mask_path), cv2.IMREAD_GRAYSCALE)

    assert metadata["method"] == "conservative_glyph"
    assert 0 < cv2.countNonZero(mask) < 1800
    assert mask[50, 40] == 255
    assert mask[50, 30] == 0


def test_text_masks_reuse_one_decoded_page(tmp_path: Path, monkeypatch) -> None:
    image_path = tmp_path / "page.png"
    image = np.full((120, 180, 3), 235, dtype=np.uint8)
    cv2.rectangle(image, (35, 25), (45, 90), (20, 20, 20), -1)
    cv2.rectangle(image, (110, 25), (120, 90), (20, 20, 20), -1)
    cv2.imwrite(str(image_path), image)

    calls = {"read": 0, "convert": 0}
    original_read = cv2.imread
    original_convert = cv2.cvtColor

    def counted_read(*args, **kwargs):
        calls["read"] += 1
        return original_read(*args, **kwargs)

    def counted_convert(*args, **kwargs):
        calls["convert"] += 1
        return original_convert(*args, **kwargs)

    monkeypatch.setattr(cv2, "imread", counted_read)
    monkeypatch.setattr(cv2, "cvtColor", counted_convert)
    source_image, source_lab = load_text_mask_source(image_path)
    for index, polygon in enumerate((
        [[20, 15], [70, 15], [70, 105], [20, 105]],
        [[95, 15], [145, 15], [145, 105], [95, 105]],
    )):
        create_text_mask(
            image_path,
            polygon,
            tmp_path / f"mask-{index}.png",
            source_image=source_image,
            source_lab=source_lab,
        )

    assert calls == {"read": 1, "convert": 1}


def test_text_mask_union_preserves_space_between_grouped_lines(tmp_path: Path) -> None:
    image_path = tmp_path / "page.png"
    mask_path = tmp_path / "grouped-mask.png"
    image = np.full((110, 160, 3), 235, dtype=np.uint8)
    cv2.rectangle(image, (30, 35), (38, 70), (20, 20, 20), -1)
    cv2.rectangle(image, (112, 35), (120, 70), (20, 20, 20), -1)
    cv2.imwrite(str(image_path), image)
    polygons = [
        [[20, 20], [52, 20], [52, 85], [20, 85]],
        [[100, 20], [132, 20], [132, 85], [100, 85]],
    ]

    metadata = create_text_mask_union(image_path, polygons, mask_path, expand=1)
    mask = cv2.imread(str(mask_path), cv2.IMREAD_GRAYSCALE)

    assert metadata["method"] == "source_polygon_union"
    assert metadata["source_count"] == 2
    assert mask[50, 30] == 255
    assert mask[50, 115] == 255
    assert mask[50, 75] == 0


def test_text_mask_union_applies_shared_balloon_constraint_once(tmp_path: Path, monkeypatch) -> None:
    image_path = tmp_path / "page.png"
    mask_path = tmp_path / "grouped-mask.png"
    image = np.full((110, 160, 3), 235, dtype=np.uint8)
    cv2.rectangle(image, (30, 35), (38, 70), (20, 20, 20), -1)
    cv2.rectangle(image, (112, 35), (120, 70), (20, 20, 20), -1)
    cv2.imwrite(str(image_path), image)
    balloon = np.zeros((110, 160), dtype=np.uint8)
    cv2.ellipse(balloon, (80, 55), (72, 48), 0, 0, 360, 255, -1)
    polygons = [
        [[20, 20], [52, 20], [52, 85], [20, 85]],
        [[100, 20], [132, 20], [132, 85], [100, 85]],
    ]
    calls = 0
    original = masking_module.apply_balloon_constraint

    def counted(*args, **kwargs):
        nonlocal calls
        calls += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(masking_module, "apply_balloon_constraint", counted)
    metadata = create_text_mask_union(
        image_path,
        polygons,
        mask_path,
        expand=1,
        balloon_mask=balloon,
    )

    assert calls == 1
    assert metadata["constraint"]["outside_pixels_after"] == 0
    assert metadata["constraint"]["status"] in {"applied", "relaxed"}
    assert metadata["constraint"]["glyph_balloon_retention"] >= 0.9


def test_text_mask_preserves_enclosed_label_background(tmp_path: Path) -> None:
    image_path = tmp_path / "label.png"
    mask_path = tmp_path / "label-mask.png"
    image = np.full((100, 100, 3), 90, dtype=np.uint8)
    cv2.rectangle(image, (20, 25), (80, 75), (240, 240, 240), -1)
    cv2.rectangle(image, (42, 35), (48, 65), (15, 15, 15), -1)
    cv2.rectangle(image, (55, 35), (61, 65), (15, 15, 15), -1)
    cv2.imwrite(str(image_path), image)
    metadata = create_text_mask(
        image_path,
        [[20, 25], [80, 25], [80, 75], [20, 75]],
        mask_path,
        expand=1,
    )
    mask = cv2.imread(str(mask_path), cv2.IMREAD_GRAYSCALE)
    assert mask[50, 45] == 255
    assert mask[50, 30] == 0
    assert metadata["enclosed_background"] is True
    assert metadata["method"] == "enclosed_background_color"


def test_text_mask_uses_expanded_polygon_for_large_stylized_text(tmp_path: Path) -> None:
    image_path = tmp_path / "title.png"
    mask_path = tmp_path / "title-mask.png"
    rng = np.random.default_rng(7)
    image = rng.integers(35, 210, size=(140, 220, 3), dtype=np.uint8)
    cv2.putText(image, "TITLE", (35, 85), cv2.FONT_HERSHEY_DUPLEX, 1.4, (250, 250, 250), 8)
    cv2.putText(image, "TITLE", (35, 85), cv2.FONT_HERSHEY_DUPLEX, 1.4, (10, 10, 10), 3)
    cv2.imwrite(str(image_path), image)

    polygon = [[25, 35], [195, 35], [195, 100], [25, 100]]
    metadata = create_text_mask(image_path, polygon, mask_path, expand=2)
    mask = cv2.imread(str(mask_path), cv2.IMREAD_GRAYSCALE)

    assert metadata["method"] == "stylized_text_polygon"
    assert metadata["coverage"] == 1.0
    assert metadata["mask_expansion"] > 1.0
    assert mask[32, 110] == 255


def test_balloon_constraint_clips_ellipse_to_resolution_adaptive_safe_interior() -> None:
    shape = (1000, 1000)
    balloon = np.zeros(shape, dtype=np.uint8)
    cv2.ellipse(balloon, (500, 500), (230, 360), 13, 0, 360, 255, -1)
    repair = np.zeros(shape, dtype=np.uint8)
    cv2.rectangle(repair, (250, 120), (750, 880), 255, -1)
    glyphs = np.zeros(shape, dtype=np.uint8)
    cv2.rectangle(glyphs, (470, 360), (530, 640), 255, -1)

    constrained, metadata = apply_balloon_constraint(
        repair,
        balloon,
        glyph_evidence=glyphs,
    )

    distance = cv2.distanceTransform((balloon > 0).astype(np.uint8), cv2.DIST_L2, 5)
    safe_interior = distance > metadata["safe_margin_px"]
    assert metadata["status"] == "applied"
    assert metadata["base_margin_px"] == 2
    assert metadata["requested_margin_px"] == 2
    assert metadata["safe_margin_px"] == 2
    assert cv2.countNonZero(constrained) > 0
    assert np.count_nonzero(constrained[~safe_interior]) == 0
    assert metadata["outside_pixels_before"] > 0
    assert metadata["outside_pixels_after"] == 0


def test_empty_balloon_constraint_fails_closed() -> None:
    repair = np.full((80, 120), 255, dtype=np.uint8)
    constrained, metadata = apply_balloon_constraint(repair, np.zeros_like(repair))

    assert cv2.countNonZero(constrained) == 0
    assert metadata["status"] == "fallback"
    assert metadata["reason"] == "empty_balloon_mask"
    assert metadata["final_mask_area"] == 0


def test_balloon_constraint_surfaces_partial_glyph_coverage_for_review() -> None:
    balloon = np.zeros((160, 200), dtype=np.uint8)
    cv2.rectangle(balloon, (20, 20), (100, 140), 255, -1)
    repair = np.zeros_like(balloon)
    glyphs = np.zeros_like(balloon)
    cv2.rectangle(repair, (60, 50), (140, 110), 255, -1)
    cv2.rectangle(glyphs, (60, 50), (140, 110), 255, -1)

    source_polygon = [[60.0, 50.0], [140.0, 50.0], [140.0, 110.0], [60.0, 110.0]]
    constrained, metadata = apply_balloon_constraint(
        repair,
        balloon,
        glyph_evidence=glyphs,
        source_polygons=[source_polygon],
    )

    assert metadata["status"] == "partial"
    assert metadata["reason"] == "core_glyph_evidence_outside_balloon"
    assert metadata["glyph_balloon_retention"] < 0.6
    assert np.count_nonzero(constrained[balloon == 0]) == 0


def test_balloon_constraint_handles_concave_instance_shape() -> None:
    shape = (300, 300)
    balloon = np.zeros(shape, dtype=np.uint8)
    concave = np.asarray(
        [[35, 35], [265, 35], [265, 265], [180, 265], [180, 120], [120, 120], [120, 265], [35, 265]],
        dtype=np.int32,
    )
    cv2.fillPoly(balloon, [concave], 255)
    repair = np.zeros(shape, dtype=np.uint8)
    cv2.rectangle(repair, (25, 25), (275, 275), 255, -1)
    glyphs = np.zeros(shape, dtype=np.uint8)
    cv2.rectangle(glyphs, (55, 70), (95, 230), 255, -1)

    constrained, metadata = apply_balloon_constraint(
        repair,
        balloon,
        glyph_evidence=glyphs,
    )

    distance = cv2.distanceTransform((balloon > 0).astype(np.uint8), cv2.DIST_L2, 5)
    safe_interior = distance > metadata["safe_margin_px"]
    assert metadata["status"] == "applied"
    assert cv2.countNonZero(constrained) > 0
    assert np.count_nonzero(constrained[~safe_interior]) == 0
    assert constrained[200, 150] == 0  # The inward notch is not repaired.


def test_balloon_constraint_handles_spiky_cloud_and_tailed_shapes() -> None:
    shape = (240, 240)
    star = np.zeros(shape, dtype=np.uint8)
    angles = np.linspace(0, 2 * np.pi, 24, endpoint=False)
    radii = np.where(np.arange(24) % 2 == 0, 102, 72)
    points = np.column_stack((120 + np.cos(angles) * radii, 120 + np.sin(angles) * radii))
    cv2.fillPoly(star, [np.rint(points).astype(np.int32)], 255)

    cloud = np.zeros(shape, dtype=np.uint8)
    for center in ((72, 84), (112, 65), (158, 78), (176, 120), (151, 165), (95, 174), (61, 139)):
        cv2.circle(cloud, center, 48, 255, -1)

    tailed = np.zeros(shape, dtype=np.uint8)
    cv2.ellipse(tailed, (118, 105), (82, 72), -8, 0, 360, 255, -1)
    cv2.fillPoly(tailed, [np.asarray([[145, 160], [187, 220], [166, 148]], dtype=np.int32)], 255)

    repair = np.full(shape, 255, dtype=np.uint8)
    glyphs = np.zeros(shape, dtype=np.uint8)
    cv2.rectangle(glyphs, (100, 78), (140, 150), 255, -1)

    for balloon in (star, cloud, tailed):
        constrained, metadata = apply_balloon_constraint(repair, balloon, glyph_evidence=glyphs)
        distance = cv2.distanceTransform((balloon > 0).astype(np.uint8), cv2.DIST_L2, 5)

        assert metadata["status"] in {"applied", "relaxed", "partial"}
        assert cv2.countNonZero(constrained) > 0
        assert np.count_nonzero(constrained[distance <= metadata["safe_margin_px"]]) == 0
        assert np.count_nonzero(constrained[balloon == 0]) == 0


def test_balloon_constraint_preserves_each_grouped_text_component(monkeypatch) -> None:
    shape = (1000, 1000)
    balloon = np.zeros(shape, dtype=np.uint8)
    cv2.circle(balloon, (500, 500), 350, 255, -1)
    large = np.zeros(shape, dtype=np.uint8)
    small = np.zeros(shape, dtype=np.uint8)
    cv2.rectangle(large, (350, 300), (650, 700), 255, -1)
    cv2.rectangle(small, (841, 470), (845, 530), 255, -1)
    repair = cv2.bitwise_or(large, small)
    monkeypatch.setattr(
        masking_module,
        "_estimate_balloon_outline_width",
        lambda *_args, **_kwargs: (12, True),
    )

    constrained, metadata = apply_balloon_constraint(
        repair,
        balloon,
        glyph_evidence=repair,
        source_polygons=[
            [[350, 300], [650, 300], [650, 700], [350, 700]],
            [[841, 470], [845, 470], [845, 530], [841, 530]],
        ],
    )
    retained_small = cv2.countNonZero(cv2.bitwise_and(constrained, small)) / cv2.countNonZero(small)

    assert metadata["requested_margin_px"] == 12
    assert metadata["safe_margin_px"] < 12
    assert retained_small >= 0.9
    assert min(metadata["component_glyph_retentions"]) >= 0.9


def test_balloon_constraint_never_sacrifices_base_margin_for_boundary_evidence() -> None:
    shape = (180, 180)
    balloon = np.zeros(shape, dtype=np.uint8)
    cv2.ellipse(balloon, (90, 90), (60, 72), 0, 0, 360, 255, -1)
    repair = balloon.copy()
    distance = cv2.distanceTransform((balloon > 0).astype(np.uint8), cv2.DIST_L2, 5)
    boundary_glyphs = np.where((distance > 0) & (distance <= 1), 255, 0).astype(np.uint8)

    constrained, metadata = apply_balloon_constraint(
        repair,
        balloon,
        glyph_evidence=boundary_glyphs,
    )

    assert metadata["requested_margin_px"] == 1
    assert metadata["safe_margin_px"] == 1
    assert metadata["status"] == "applied"
    assert "reason" not in metadata
    assert metadata["interior_glyph_area"] == 0
    assert np.count_nonzero(constrained[balloon == 0]) == 0
    assert np.count_nonzero(constrained[distance <= 1]) == 0


def test_text_mask_rectangle_corners_are_clipped_to_ellipse_safe_interior(tmp_path: Path) -> None:
    image_path = tmp_path / "ellipse-balloon.png"
    mask_path = tmp_path / "ellipse-text-mask.png"
    image = np.full((140, 140, 3), 238, dtype=np.uint8)
    cv2.rectangle(image, (60, 35), (66, 105), (15, 15, 15), -1)
    cv2.rectangle(image, (77, 35), (83, 105), (15, 15, 15), -1)
    cv2.imwrite(str(image_path), image)
    balloon = np.zeros((140, 140), dtype=np.uint8)
    cv2.ellipse(balloon, (70, 70), (42, 58), 0, 0, 360, 255, -1)
    polygon = [[24, 8], [116, 8], [116, 132], [24, 132]]

    metadata = create_text_mask(
        image_path,
        polygon,
        mask_path,
        expand=2,
        balloon_mask=balloon,
        balloon_context={"bubble_id": "bubble-ellipse", "bubble_confidence": 0.98},
    )
    mask = cv2.imread(str(mask_path), cv2.IMREAD_GRAYSCALE)
    constraint = metadata["constraint"]
    distance = cv2.distanceTransform((balloon > 0).astype(np.uint8), cv2.DIST_L2, 5)
    safe_interior = distance > constraint["safe_margin_px"]

    assert metadata["method"] == "uniform_background_polygon"
    assert constraint["status"] in {"applied", "relaxed"}
    assert constraint["bubble_id"] == "bubble-ellipse"
    assert np.count_nonzero(mask[~safe_interior]) == 0
    assert mask[70, 63] == 255
    assert mask[8, 24] == 0
    assert mask[132, 116] == 0


def test_uniform_fill_uses_unmasked_background_inside_label() -> None:
    image = np.full((80, 140, 3), 45, dtype=np.uint8)
    cv2.rectangle(image, (20, 20), (120, 60), (245, 245, 245), -1)
    mask = np.zeros((80, 140), dtype=np.uint8)
    cv2.rectangle(mask, (40, 28), (48, 52), 255, -1)
    cv2.rectangle(mask, (62, 28), (70, 52), 255, -1)
    cv2.rectangle(mask, (84, 28), (92, 52), 255, -1)

    result = OpenCVInpainter._uniform_fill(image, mask)

    assert np.min(result[35, 44]) > 225
    assert np.min(result[35, 66]) > 225


def test_opencv_inpainting_preserves_icc_and_pixels_outside_mask(tmp_path: Path) -> None:
    source_path = tmp_path / "source.png"
    mask_path = tmp_path / "mask.png"
    output_path = tmp_path / "output.png"
    profile = _srgb_profile()
    source = np.zeros((48, 64, 3), dtype=np.uint8)
    source[..., 0] = np.arange(64, dtype=np.uint8)
    source[..., 1] = np.arange(48, dtype=np.uint8)[:, None]
    Image.fromarray(source).save(source_path, "PNG", icc_profile=profile)
    mask = np.zeros((48, 64), dtype=np.uint8)
    mask[16:32, 24:40] = 255
    cv2.imwrite(str(mask_path), mask)

    # Uniform-fill deliberately blurs its edge and would bleed beyond the mask
    # unless the provider enforces a final mask-only composite.
    OpenCVInpainter().inpaint(source_path, mask_path, output_path, "bubble_simple")

    with Image.open(output_path) as output_image:
        assert output_image.info["icc_profile"] == profile
        output = np.asarray(output_image.convert("RGB"))
    assert np.array_equal(output[mask == 0], source[mask == 0])


def test_lama_composites_only_inside_mask_and_preserves_icc(tmp_path: Path) -> None:
    source_path = tmp_path / "source.png"
    mask_path = tmp_path / "mask.png"
    output_path = tmp_path / "output.png"
    profile = _srgb_profile()
    source = Image.new("RGB", (40, 30), (30, 80, 120))
    source.save(source_path, "PNG", icc_profile=profile)
    mask_array = np.zeros((30, 40), dtype=np.uint8)
    mask_array[10:20, 12:28] = 255
    Image.fromarray(mask_array).save(mask_path, "PNG")

    provider = SimpleLaMaInpainter()
    provider._model = lambda _image, _mask: Image.new("RGB", source.size, (240, 10, 20))
    provider.state = ProviderState.READY
    provider.inpaint(source_path, mask_path, output_path, "background_complex")

    with Image.open(output_path) as output_image:
        assert output_image.info["icc_profile"] == profile
        output = np.asarray(output_image.convert("RGB"))
    original = np.asarray(source)
    assert np.array_equal(output[mask_array == 0], original[mask_array == 0])
    assert np.all(output[mask_array > 0] == (240, 10, 20))


def test_legacy_hybrid_provider_routes_simple_regions_to_complex_lama(tmp_path: Path) -> None:
    calls: list[str] = []

    class FakeLama:
        def inpaint(
            self,
            _image_path: Path,
            _mask_path: Path,
            output_path: Path,
            region_type: str,
        ) -> Path:
            calls.append(region_type)
            return output_path

    provider = HybridInpainter()
    provider._lama = FakeLama()  # type: ignore[assignment]
    provider.state = ProviderState.READY

    output = tmp_path / "output.png"
    assert provider.inpaint(tmp_path / "source.png", tmp_path / "mask.png", output, "bubble_simple") == output
    assert calls == ["background_complex"]
