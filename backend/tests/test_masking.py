from pathlib import Path

import cv2
import numpy as np
from app.services.base import ProviderState
from app.services.inpainting.masking import (
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
