from __future__ import annotations

import hashlib
from dataclasses import replace
from pathlib import Path

import cv2
import numpy as np
from app.services.base import DetectionResult
from app.services.detection.bubbles import BubbleInstance
from app.utils.geometry import bbox_to_polygon

MIN_TRUSTED_BALLOON_CONFIDENCE = 0.75


def group_text_regions_by_bubbles(
    regions: list[DetectionResult],
    bubbles: list[BubbleInstance],
    *,
    page_key: str = "",
    image_path: Path | None = None,
    min_bubble_confidence: float = MIN_TRUSTED_BALLOON_CONFIDENCE,
    min_containment: float = 0.55,
    min_core_containment: float = 0.8,
    ambiguity_margin: float = 0.12,
    max_second_containment: float = 0.45,
    mask_padding: int = 3,
    split_connected_instances: bool = True,
    split_max_neck_ratio: float = 0.22,
    split_min_boundary_coverage: float = 0.7,
    split_min_boundary_run: float = 0.65,
) -> list[DetectionResult]:
    """Assign text polygons to balloon instances and merge only equal IDs.

    A missing or uncertain balloon assignment is intentionally never used as a
    grouping key. This keeps free-standing sound effects and overlapping text
    boxes outside balloons independent.
    """
    assigned = assign_text_to_bubbles(
        regions,
        bubbles,
        page_key=page_key,
        min_bubble_confidence=min_bubble_confidence,
        min_containment=min_containment,
        min_core_containment=min_core_containment,
        ambiguity_margin=ambiguity_margin,
        max_second_containment=max_second_containment,
        mask_padding=mask_padding,
    )
    grouped: dict[str, list[tuple[int, DetectionResult]]] = {}
    order: list[str] = []
    for index, region in enumerate(assigned):
        key = region.bubble_id or f"outside:{index}"
        if key not in grouped:
            order.append(key)
            grouped[key] = []
        grouped[key].append((index, region))

    if split_connected_instances:
        if not 0 <= split_max_neck_ratio <= 0.5:
            raise ValueError("split_max_neck_ratio must be between 0 and 0.5")
        if not 0 <= split_min_boundary_coverage <= 1 or not 0 <= split_min_boundary_run <= 1:
            raise ValueError("boundary split thresholds must be between 0 and 1")

    image = None
    if split_connected_instances and image_path is not None and image_path.is_file():
        image = cv2.imread(str(image_path), cv2.IMREAD_GRAYSCALE)
    bubbles_by_id = {_stable_bubble_id(page_key, bubble): bubble for bubble in bubbles}
    output: list[DetectionResult] = []
    for key in order:
        group = grouped[key]
        split_groups = [group]
        bubble = bubbles_by_id.get(key)
        if split_connected_instances and bubble is not None and len(group) > 1:
            split_groups = _split_connected_balloon_group(
                group,
                bubble,
                page_key=page_key,
                image=image,
                max_neck_ratio=split_max_neck_ratio,
                min_boundary_coverage=split_min_boundary_coverage,
                min_boundary_run=split_min_boundary_run,
            )
        output.extend(_merge_balloon_group(item) for item in split_groups)
    return output


def assign_text_to_bubbles(
    regions: list[DetectionResult],
    bubbles: list[BubbleInstance],
    *,
    page_key: str = "",
    min_bubble_confidence: float = MIN_TRUSTED_BALLOON_CONFIDENCE,
    min_containment: float = 0.55,
    min_core_containment: float = 0.8,
    ambiguity_margin: float = 0.12,
    max_second_containment: float = 0.45,
    mask_padding: int = 3,
) -> list[DetectionResult]:
    min_bubble_confidence = max(MIN_TRUSTED_BALLOON_CONFIDENCE, min_bubble_confidence)
    bubble_ids = {bubble.instance_id: _stable_bubble_id(page_key, bubble) for bubble in bubbles}
    bubbles_by_instance_id = {bubble.instance_id: bubble for bubble in bubbles}
    output: list[DetectionResult] = []
    for region in regions:
        if not _valid_polygon(region.polygon):
            output.append(_with_balloon_assignment(region, "outside", None, reason="invalid_polygon"))
            continue
        centroid = _polygon_centroid(region.polygon)
        core = _scale_polygon(region.polygon, centroid, 0.7)
        candidates: list[dict[str, float | str]] = []
        for bubble in bubbles:
            if bubble.confidence < min_bubble_confidence:
                continue
            coverage = bubble.intersection_ratio(region.polygon, padding=mask_padding)
            core_coverage = bubble.intersection_ratio(core)
            center_inside = bubble.contains_point(centroid)
            valid = (
                center_inside
                and coverage >= min_containment
                and core_coverage >= min_core_containment
            ) or (coverage >= 0.82 and core_coverage >= 0.9)
            if not valid:
                continue
            score = 0.55 * coverage + 0.35 * core_coverage + 0.1 * bubble.confidence
            candidates.append(
                {
                    "instance_id": bubble.instance_id,
                    "coverage": coverage,
                    "core_coverage": core_coverage,
                    "score": score,
                    "balloon_confidence": bubble.confidence,
                }
            )
        candidates.sort(key=lambda item: float(item["score"]), reverse=True)
        if not candidates:
            output.append(_with_balloon_assignment(region, "outside", None, reason="no_matching_balloon"))
            continue
        best = candidates[0]
        second = candidates[1] if len(candidates) > 1 else None
        ambiguous = second is not None and (
            float(best["score"]) - float(second["score"]) < ambiguity_margin
            or float(second["coverage"]) >= max_second_containment
        )
        if ambiguous:
            output.append(
                _with_balloon_assignment(
                    region,
                    "ambiguous",
                    None,
                    candidate=best,
                    second_score=float(second["score"]),
                )
            )
            continue
        instance_id = str(best["instance_id"])
        output.append(
            _with_balloon_assignment(
                region,
                "assigned",
                bubble_ids[instance_id],
                candidate=best,
                second_score=float(second["score"]) if second else None,
                bubble=bubbles_by_instance_id[instance_id],
            )
        )
    return output


def _valid_polygon(polygon: list[list[float]]) -> bool:
    try:
        points = np.asarray(polygon, dtype=np.float64)
    except (TypeError, ValueError):
        return False
    return bool(
        len(points) >= 3
        and points.shape == (len(points), 2)
        and np.all(np.isfinite(points))
        and abs(cv2.contourArea(points.astype(np.float32))) > 1e-6
    )


def _stable_bubble_id(page_key: str, bubble: BubbleInstance) -> str:
    """Build a stable, page-scoped ID without persisting model-local indexes."""

    quantized_bbox = [round(float(value) / 4) * 4 for value in bubble.bbox]
    payload = (
        f"{page_key}|{','.join(str(value) for value in quantized_bbox)}|"
        f"{bubble.mask_origin[0]},{bubble.mask_origin[1]}|{_mask_fingerprint(bubble.mask)}"
    )
    digest = hashlib.blake2s(payload.encode("utf-8"), digest_size=6).hexdigest()
    return f"bubble-{digest}"


def _mask_fingerprint(mask: np.ndarray) -> str:
    binary = np.ascontiguousarray(np.asarray(mask) > 0, dtype=np.uint8)
    shape = np.asarray(binary.shape, dtype="<i8").tobytes()
    packed = np.packbits(binary, bitorder="little").tobytes()
    return hashlib.blake2s(shape + packed, digest_size=8).hexdigest()


def _polygon_centroid(polygon: list[list[float]]) -> tuple[float, float]:
    points = np.asarray(polygon, dtype=np.float64)
    shifted = np.roll(points, -1, axis=0)
    cross = points[:, 0] * shifted[:, 1] - shifted[:, 0] * points[:, 1]
    signed_area = float(np.sum(cross) / 2)
    if abs(signed_area) < 1e-8:
        return float(np.mean(points[:, 0])), float(np.mean(points[:, 1]))
    scale = 1 / (6 * signed_area)
    return (
        float(np.sum((points[:, 0] + shifted[:, 0]) * cross) * scale),
        float(np.sum((points[:, 1] + shifted[:, 1]) * cross) * scale),
    )


def _scale_polygon(
    polygon: list[list[float]],
    center: tuple[float, float],
    scale: float,
) -> list[list[float]]:
    center_array = np.asarray(center, dtype=np.float64)
    points = np.asarray(polygon, dtype=np.float64)
    return (center_array + (points - center_array) * scale).tolist()


def _with_balloon_assignment(
    region: DetectionResult,
    status: str,
    bubble_id: str | None,
    *,
    candidate: dict[str, float | str] | None = None,
    second_score: float | None = None,
    reason: str | None = None,
    bubble: BubbleInstance | None = None,
) -> DetectionResult:
    assignment: dict[str, object] = {"status": status, "bubble_id": bubble_id}
    if candidate is not None:
        assignment.update(
            {
                "coverage": round(float(candidate["coverage"]), 4),
                "core_coverage": round(float(candidate["core_coverage"]), 4),
                "score": round(float(candidate["score"]), 4),
                "balloon_confidence": round(float(candidate["balloon_confidence"]), 4),
            }
        )
    if second_score is not None:
        assignment["second_score"] = round(float(second_score), 4)
    if reason:
        assignment["reason"] = reason
    return replace(
        region,
        bubble_id=bubble_id,
        metadata={**region.metadata, "balloon_assignment": assignment},
        balloon_mask=bubble.mask.copy() if bubble is not None else None,
        balloon_mask_origin=bubble.mask_origin if bubble is not None else None,
        balloon_mask_id=bubble_id if bubble is not None else None,
        balloon_mask_parent_id=None,
        balloon_mask_confidence=bubble.confidence if bubble is not None else None,
    )


def _split_connected_balloon_group(
    group: list[tuple[int, DetectionResult]],
    bubble: BubbleInstance,
    *,
    page_key: str,
    image: np.ndarray | None,
    max_neck_ratio: float,
    min_boundary_coverage: float,
    min_boundary_run: float,
) -> list[list[tuple[int, DetectionResult]]]:
    """Split a model instance only when topology or its visible outline proves it contains two balloons."""

    parent_bubble_id = group[0][1].bubble_id
    if parent_bubble_id is None:
        return [group]

    neck_split = _split_group_at_mask_neck(group, bubble, max_neck_ratio=max_neck_ratio)
    if neck_split is not None:
        groups, details, child_masks = neck_split
        return _tag_balloon_subgroups(
            groups,
            bubble=bubble,
            child_masks=child_masks,
            page_key=page_key,
            parent_bubble_id=parent_bubble_id,
            method="mask_neck",
            details=details,
        )

    boundary_split = _split_group_at_visible_boundary(
        group,
        bubble,
        image=image,
        min_boundary_coverage=min_boundary_coverage,
        min_boundary_run=min_boundary_run,
    )
    if boundary_split is None:
        return [group]
    groups, details, child_masks = boundary_split
    return _tag_balloon_subgroups(
        groups,
        bubble=bubble,
        child_masks=child_masks,
        page_key=page_key,
        parent_bubble_id=parent_bubble_id,
        method="text_gap_boundary",
        details=details,
    )


def _split_group_at_mask_neck(
    group: list[tuple[int, DetectionResult]],
    bubble: BubbleInstance,
    *,
    max_neck_ratio: float,
) -> tuple[
    list[list[tuple[int, DetectionResult]]],
    dict[str, object],
    list[np.ndarray],
] | None:
    """Find narrow bridges while ignoring mask lobes that contain no detected text."""

    if max_neck_ratio <= 0 or bubble.mask.size == 0:
        return None
    mask = (bubble.mask > 0).astype(np.uint8)
    # Compact masks can touch every crop edge (and synthetic rectangular masks
    # can contain no zero pixel at all). Padding supplies the outside background
    # that OpenCV's distance transform requires for finite edge distances.
    padded_mask = np.pad(mask, 1, mode="constant")
    distance = cv2.distanceTransform(padded_mask, cv2.DIST_L2, 5)[1:-1, 1:-1]
    peak_radius = float(distance.max())
    max_radius = int(np.floor(peak_radius * max_neck_ratio))
    if max_radius < 2:
        return None

    minimum_component_area = max(24, int(np.count_nonzero(mask) * 0.08))
    origin = np.asarray(bubble.mask_origin, dtype=np.float64)
    for radius in range(2, max_radius + 1):
        eroded = (distance > radius).astype(np.uint8)
        component_count, labels, stats, _ = cv2.connectedComponentsWithStats(eroded, 8)
        meaningful_labels = {
            label
            for label in range(1, component_count)
            if int(stats[label, cv2.CC_STAT_AREA]) >= minimum_component_area
        }
        if len(meaningful_labels) < 2:
            continue

        members_by_label: dict[int, list[tuple[int, DetectionResult]]] = {}
        uncertain = False
        for item in group:
            region = item[1]
            center = _polygon_centroid(region.polygon)
            core_points = np.asarray(_scale_polygon(region.polygon, center, 0.7), dtype=np.float64) - origin
            core_mask = np.zeros_like(mask)
            cv2.fillPoly(core_mask, [np.rint(core_points).astype(np.int32)], 1)
            values = labels[core_mask > 0]
            original_core_area = int(np.count_nonzero((core_mask > 0) & (mask > 0)))
            counts = {
                label: int(np.count_nonzero(values == label))
                for label in meaningful_labels
                if np.any(values == label)
            }
            total = sum(counts.values())
            if not counts or total <= 0 or original_core_area <= 0:
                uncertain = True
                break
            best_label, best_count = max(counts.items(), key=lambda item: item[1])
            if best_count / total < 0.8 or best_count / original_core_area < 0.4:
                uncertain = True
                break
            members_by_label.setdefault(best_label, []).append(item)
        if uncertain or len(members_by_label) < 2:
            continue

        ordered = sorted(
            members_by_label.items(),
            key=lambda item: min(index for index, _ in item[1]),
        )
        groups = [members for _, members in ordered]
        seeds = [(labels == label).astype(np.uint8) for label, _ in ordered]
        child_masks = _partition_mask_from_seeds(mask, seeds)
        return (
            groups,
            {
                "erosion_radius": radius,
                "neck_ratio": round(radius / peak_radius, 4),
            },
            child_masks,
        )
    return None


def _split_group_at_visible_boundary(
    group: list[tuple[int, DetectionResult]],
    bubble: BubbleInstance,
    *,
    image: np.ndarray | None,
    min_boundary_coverage: float,
    min_boundary_run: float,
) -> tuple[
    list[list[tuple[int, DetectionResult]]],
    dict[str, object],
    list[np.ndarray],
] | None:
    """Confirm a two-dimensional text-layout cut with a visible balloon seam.

    OCR orientation describes how glyphs are read, not how two touching
    balloons are arranged. Two vertical text blocks can be side-by-side or
    stacked, so candidate cuts must be tested on both page axes. Candidate
    gaps use shrunken text cores because Paddle's expanded polygons often
    overlap across the real seam even when their glyphs do not.
    """

    if image is None or len(group) < 2:
        return None
    orientation_counts: dict[str, int] = {}
    for _, region in group:
        orientation_counts[region.orientation] = orientation_counts.get(region.orientation, 0) + 1
    text_orientation = max(orientation_counts, key=orientation_counts.get)
    dominant = [item for item in group if item[1].orientation == text_orientation]
    if text_orientation not in {"vertical", "horizontal"} or len(dominant) / len(group) < 0.8:
        return None

    evidence_maps = _bubble_boundary_evidence(image, bubble, [region for _, region in group])
    if evidence_maps is None:
        return None
    boundary_evidence, internal_evidence, boundary_support, mask_support = evidence_maps

    def geometry(
        region: DetectionResult,
        split_orientation: str,
    ) -> tuple[float, float, float, float, float, float]:
        x, y, width, height = region.bbox
        center = _polygon_centroid(region.polygon)
        core = np.asarray(_scale_polygon(region.polygon, center, 0.7), dtype=np.float64)
        core_x0, core_y0 = np.min(core, axis=0)
        core_x1, core_y1 = np.max(core, axis=0)
        if split_orientation == "vertical":
            return (
                float(core_x0),
                float(core_x1),
                y,
                y + height,
                float(core_x1 - core_x0),
                height,
            )
        return (
            float(core_y0),
            float(core_y1),
            x,
            x + width,
            float(core_y1 - core_y0),
            width,
        )

    candidates: list[
        tuple[
            float,
            str,
            list[list[tuple[int, DetectionResult]]],
            list[dict[str, object]],
            list[np.ndarray],
        ]
    ] = []
    orientations = [text_orientation, "horizontal" if text_orientation == "vertical" else "vertical"]
    for split_orientation in orientations:
        ordered = sorted(
            dominant,
            key=lambda item: sum(geometry(item[1], split_orientation)[:2]) / 2,
        )
        short_side = float(
            np.median([geometry(region, split_orientation)[4] for _, region in ordered])
        )
        long_side = float(
            np.median([geometry(region, split_orientation)[5] for _, region in ordered])
        )
        minimum_gap = max(2.0, short_side * 0.04)
        minimum_cap = max(12.0, long_side * 0.15)
        half_width = max(4.0, short_side * 0.2)
        accepted: list[dict[str, object]] = []

        for cut_index in range(1, len(ordered)):
            first, second = ordered[:cut_index], ordered[cut_index:]
            first_edge = max(geometry(region, split_orientation)[1] for _, region in first)
            second_edge = min(geometry(region, split_orientation)[0] for _, region in second)
            gap = second_edge - first_edge
            if gap < minimum_gap:
                continue

            first_lead = float(
                np.median([geometry(region, split_orientation)[2] for _, region in first])
            )
            second_lead = float(
                np.median([geometry(region, split_orientation)[2] for _, region in second])
            )
            first_trail = float(
                np.median([geometry(region, split_orientation)[3] for _, region in first])
            )
            second_trail = float(
                np.median([geometry(region, split_orientation)[3] for _, region in second])
            )
            leading_cap = (min(first_lead, second_lead), max(first_lead, second_lead))
            trailing_cap = (min(first_trail, second_trail), max(first_trail, second_trail))
            cut = (first_edge + second_edge) / 2
            cap_candidates: list[dict[str, object]] = []
            for cap_kind, cap in (("leading", leading_cap), ("trailing", trailing_cap)):
                cap_length = cap[1] - cap[0]
                if cap_length < minimum_cap:
                    continue
                coverage, run = _boundary_path_metrics(
                    boundary_evidence,
                    bubble,
                    orientation=split_orientation,
                    cut=cut,
                    cap=cap,
                    half_width=half_width,
                )
                boundary_source = "mask_boundary"
                if coverage < min_boundary_coverage or run < min_boundary_run:
                    coverage, run = _anchored_internal_path_metrics(
                        internal_evidence,
                        boundary_support,
                        mask_support,
                        bubble,
                        orientation=split_orientation,
                        cut=cut,
                        cap=cap,
                        cap_kind=cap_kind,
                        half_width=half_width,
                    )
                    boundary_source = "anchored_internal_line"
                    if coverage < max(0.9, min_boundary_coverage) or run < max(
                        0.85, min_boundary_run
                    ):
                        continue
                cap_candidates.append(
                    {
                        "cut": round(cut, 3),
                        "gap": round(gap, 3),
                        "cap_kind": cap_kind,
                        "cap_length": round(cap_length, 3),
                        "boundary_coverage": round(coverage, 4),
                        "boundary_run": round(run, 4),
                        "boundary_source": boundary_source,
                    }
                )
            if cap_candidates:
                accepted.append(
                    max(
                        cap_candidates,
                        key=lambda item: min(
                            float(item["boundary_coverage"]),
                            float(item["boundary_run"]),
                        ),
                    )
                )

        if not accepted:
            continue

        cuts = sorted(float(item["cut"]) for item in accepted)
        groups_by_bucket: dict[int, list[tuple[int, DetectionResult]]] = {}
        for item in group:
            region = item[1]
            axis_start, axis_end = geometry(region, split_orientation)[:2]
            axis_center = (axis_start + axis_end) / 2
            bucket = sum(axis_center > cut for cut in cuts)
            groups_by_bucket.setdefault(bucket, []).append(item)
        if len(groups_by_bucket) < 2:
            continue
        ordered_buckets = sorted(
            groups_by_bucket.items(),
            key=lambda item: min(index for index, _ in item[1]),
        )
        groups = [members for _, members in ordered_buckets]
        present_buckets = [bucket for bucket, _ in ordered_buckets]
        child_masks = _partition_mask_at_axis_cuts(
            (bubble.mask > 0).astype(np.uint8),
            bubble.mask_origin,
            orientation=split_orientation,
            cuts=cuts,
            present_buckets=present_buckets,
        )
        strength = min(
            min(float(item["boundary_coverage"]), float(item["boundary_run"]))
            for item in accepted
        )
        candidates.append((strength, split_orientation, groups, accepted, child_masks))

    if not candidates:
        return None
    _, split_orientation, groups, accepted, child_masks = max(
        candidates,
        key=lambda item: (item[0], len(item[2])),
    )
    return groups, {"orientation": split_orientation, "cuts": accepted}, child_masks


def _partition_mask_from_seeds(mask: np.ndarray, seeds: list[np.ndarray]) -> list[np.ndarray]:
    """Return mutually exclusive children whose union is the parent mask."""

    if len(seeds) < 2 or any(seed.shape != mask.shape or not np.any(seed) for seed in seeds):
        raise ValueError("Balloon split seeds must be non-empty and match the parent mask")
    distances = np.stack(
        [
            cv2.distanceTransform(np.where(seed > 0, 0, 1).astype(np.uint8), cv2.DIST_L2, 5)
            for seed in seeds
        ],
        axis=0,
    )
    owners = np.argmin(distances, axis=0)
    parent = mask > 0
    return [((owners == index) & parent).astype(np.uint8) for index in range(len(seeds))]


def _partition_mask_at_axis_cuts(
    mask: np.ndarray,
    origin: tuple[int, int],
    *,
    orientation: str,
    cuts: list[float],
    present_buckets: list[int],
) -> list[np.ndarray]:
    """Partition a connected instance at confirmed visible boundary cuts."""

    height, width = mask.shape
    if orientation == "vertical":
        coordinates = np.arange(width, dtype=np.float32) + origin[0] + 0.5
        raw_buckets = np.searchsorted(np.asarray(cuts), coordinates, side="left")
        bucket_map = np.broadcast_to(raw_buckets[None, :], (height, width))
    elif orientation == "horizontal":
        coordinates = np.arange(height, dtype=np.float32) + origin[1] + 0.5
        raw_buckets = np.searchsorted(np.asarray(cuts), coordinates, side="left")
        bucket_map = np.broadcast_to(raw_buckets[:, None], (height, width))
    else:
        raise ValueError(f"Unsupported balloon split orientation: {orientation}")

    # A cut can create an empty text bucket. Assign its pixels to the nearest
    # populated bucket so the child masks remain a lossless parent partition.
    present = np.asarray(present_buckets, dtype=np.int32)
    nearest = present[np.argmin(np.abs(bucket_map[..., None] - present), axis=2)]
    parent = mask > 0
    return [((nearest == bucket) & parent).astype(np.uint8) for bucket in present_buckets]


def _bubble_boundary_evidence(
    image: np.ndarray,
    bubble: BubbleInstance,
    regions: list[DetectionResult],
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray] | None:
    origin_x, origin_y = bubble.mask_origin
    height, width = bubble.mask.shape
    if (
        origin_x < 0
        or origin_y < 0
        or origin_x + width > image.shape[1]
        or origin_y + height > image.shape[0]
    ):
        return None
    crop = image[origin_y : origin_y + height, origin_x : origin_x + width]
    mask = (bubble.mask > 0).astype(np.uint8)
    # Padding makes crop edges real mask edges even when the compact mask is
    # completely filled. Without it, OpenCV treats an all-one crop as having
    # no outside boundary at all.
    padded_mask = np.pad(mask, 2, mode="constant")
    dilated = cv2.dilate(padded_mask, np.ones((3, 3), dtype=np.uint8))[2:-2, 2:-2]
    eroded = cv2.erode(padded_mask, np.ones((5, 5), dtype=np.uint8))[2:-2, 2:-2]
    boundary = (dilated > 0) & (eroded == 0)

    text_core = np.zeros_like(mask)
    origin = np.asarray((origin_x, origin_y), dtype=np.float64)
    for region in regions:
        center = _polygon_centroid(region.polygon)
        points = np.asarray(_scale_polygon(region.polygon, center, 0.7), dtype=np.float64) - origin
        cv2.fillPoly(text_core, [np.rint(points).astype(np.int32)], 1)
    ink = (crop < 180) & (text_core == 0)
    return (
        (ink & boundary).astype(np.uint8),
        (ink & (dilated > 0)).astype(np.uint8),
        boundary.astype(np.uint8),
        (dilated > 0).astype(np.uint8),
    )


def _boundary_path_metrics(
    evidence: np.ndarray,
    bubble: BubbleInstance,
    *,
    orientation: str,
    cut: float,
    cap: tuple[float, float],
    half_width: float,
) -> tuple[float, float]:
    path_map = _oriented_path_map(
        evidence,
        bubble,
        orientation=orientation,
        cut=cut,
        flow_range=cap,
        half_width=half_width,
    )
    if path_map.shape[0] < 2 or path_map.shape[1] == 0:
        return 0.0, 0.0
    return _maximum_path_coverage(path_map)


def _anchored_internal_path_metrics(
    internal_evidence: np.ndarray,
    boundary_support: np.ndarray,
    mask_support: np.ndarray,
    bubble: BubbleInstance,
    *,
    orientation: str,
    cut: float,
    cap: tuple[float, float],
    cap_kind: str,
    half_width: float,
) -> tuple[float, float]:
    origin_x, origin_y = bubble.mask_origin
    height, width = internal_evidence.shape
    full_flow_range = (
        (float(origin_y), float(origin_y + height))
        if orientation == "vertical"
        else (float(origin_x), float(origin_x + width))
    )
    internal_map = _oriented_path_map(
        internal_evidence,
        bubble,
        orientation=orientation,
        cut=cut,
        flow_range=full_flow_range,
        half_width=half_width,
    )
    boundary_map = _oriented_path_map(
        boundary_support,
        bubble,
        orientation=orientation,
        cut=cut,
        flow_range=full_flow_range,
        half_width=half_width,
    )
    support_map = _oriented_path_map(
        mask_support,
        bubble,
        orientation=orientation,
        cut=cut,
        flow_range=full_flow_range,
        half_width=half_width,
    )
    if internal_map.shape[0] < 2 or internal_map.shape[1] == 0:
        return 0.0, 0.0
    support_rows = np.flatnonzero(np.any(support_map > 0, axis=1))
    if len(support_rows) == 0:
        return 0.0, 0.0

    flow_origin = full_flow_range[0]
    cap_start = max(0, int(np.floor(cap[0] - flow_origin)))
    cap_end = min(internal_map.shape[0], int(np.ceil(cap[1] - flow_origin)))
    if cap_kind == "leading":
        start, end = int(support_rows[0]), cap_end
        anchor_at_start = True
    else:
        start, end = cap_start, int(support_rows[-1]) + 1
        anchor_at_start = False
    if end - start < 2:
        return 0.0, 0.0

    segment = internal_map[start:end]
    anchor_depth = max(4, int(np.ceil(len(segment) * 0.08)))
    anchor_slice = slice(0, min(len(segment), anchor_depth)) if anchor_at_start else slice(-anchor_depth, None)
    if not np.any(segment[anchor_slice] & boundary_map[start:end][anchor_slice]):
        return 0.0, 0.0
    return _maximum_path_coverage(segment)


def _oriented_path_map(
    evidence: np.ndarray,
    bubble: BubbleInstance,
    *,
    orientation: str,
    cut: float,
    flow_range: tuple[float, float],
    half_width: float,
) -> np.ndarray:
    origin_x, origin_y = bubble.mask_origin
    height, width = evidence.shape
    if orientation == "vertical":
        left = max(0, int(np.floor(cut - half_width)) - origin_x)
        right = min(width, int(np.ceil(cut + half_width)) - origin_x + 1)
        top = max(0, int(np.floor(flow_range[0])) - origin_y)
        bottom = min(height, int(np.ceil(flow_range[1])) - origin_y)
        return evidence[top:bottom, left:right]
    if orientation == "horizontal":
        left = max(0, int(np.floor(flow_range[0])) - origin_x)
        right = min(width, int(np.ceil(flow_range[1])) - origin_x)
        top = max(0, int(np.floor(cut - half_width)) - origin_y)
        bottom = min(height, int(np.ceil(cut + half_width)) - origin_y + 1)
        return evidence[top:bottom, left:right].T
    return np.zeros((0, 0), dtype=np.uint8)


def _maximum_path_coverage(path_map: np.ndarray) -> tuple[float, float]:
    rows, columns = path_map.shape
    scores = path_map[0].astype(np.float32)
    backtrack = np.zeros((rows, columns), dtype=np.int32)
    for row in range(1, rows):
        previous = scores
        scores = np.empty(columns, dtype=np.float32)
        for column in range(columns):
            start, end = max(0, column - 1), min(columns, column + 2)
            offset = int(np.argmax(previous[start:end]))
            best_column = start + offset
            scores[column] = previous[best_column] + float(path_map[row, column])
            backtrack[row, column] = best_column

    column = int(np.argmax(scores))
    matches = np.zeros(rows, dtype=bool)
    for row in range(rows - 1, -1, -1):
        matches[row] = bool(path_map[row, column])
        if row:
            column = int(backtrack[row, column])
    coverage = float(np.count_nonzero(matches) / rows)
    positions = np.flatnonzero(matches)
    if len(positions) == 0:
        return coverage, 0.0
    longest = 1
    start = previous_position = int(positions[0])
    for position_value in positions[1:]:
        position = int(position_value)
        if position - previous_position > 3:
            longest = max(longest, previous_position - start + 1)
            start = position
        previous_position = position
    longest = max(longest, previous_position - start + 1)
    return coverage, float(longest / rows)


def _tag_balloon_subgroups(
    groups: list[list[tuple[int, DetectionResult]]],
    *,
    bubble: BubbleInstance,
    child_masks: list[np.ndarray],
    page_key: str,
    parent_bubble_id: str,
    method: str,
    details: dict[str, object],
) -> list[list[tuple[int, DetectionResult]]]:
    if len(groups) != len(child_masks):
        raise ValueError("Every split balloon group needs one child mask")
    paired = sorted(
        zip(groups, child_masks, strict=True),
        key=lambda item: min(index for index, _ in item[0]),
    )
    output: list[list[tuple[int, DetectionResult]]] = []
    for child_index, (members, child_mask) in enumerate(paired):
        compact_mask, child_origin, child_bbox = _compact_child_mask(child_mask, bubble.mask_origin)
        child_bubble_id = _stable_child_bubble_id(
            page_key,
            parent_bubble_id,
            child_bbox,
            compact_mask,
        )
        split_info = {
            "method": method,
            "parent_bubble_id": parent_bubble_id,
            "child_index": child_index,
            "child_count": len(paired),
            "child_bbox": child_bbox,
            **details,
        }
        tagged: list[tuple[int, DetectionResult]] = []
        for original_index, region in members:
            metadata = dict(region.metadata)
            assignment_value = metadata.get("balloon_assignment")
            assignment = dict(assignment_value) if isinstance(assignment_value, dict) else {}
            assignment.update(
                {
                    "bubble_id": child_bubble_id,
                    "parent_bubble_id": parent_bubble_id,
                }
            )
            metadata["balloon_assignment"] = assignment
            metadata["instance_split"] = split_info
            tagged.append(
                (
                    original_index,
                    replace(
                        region,
                        bubble_id=child_bubble_id,
                        metadata=metadata,
                        balloon_mask=compact_mask.copy(),
                        balloon_mask_origin=child_origin,
                        balloon_mask_id=child_bubble_id,
                        balloon_mask_parent_id=parent_bubble_id,
                        balloon_mask_confidence=bubble.confidence,
                    ),
                )
            )
        output.append(tagged)
    return output


def _compact_child_mask(
    mask: np.ndarray,
    parent_origin: tuple[int, int],
) -> tuple[np.ndarray, tuple[int, int], list[float]]:
    rows, columns = np.nonzero(mask)
    if not len(rows):
        raise ValueError("Split balloon child mask cannot be empty")
    left, right = int(columns.min()), int(columns.max()) + 1
    top, bottom = int(rows.min()), int(rows.max()) + 1
    origin = (parent_origin[0] + left, parent_origin[1] + top)
    compact = (mask[top:bottom, left:right] > 0).astype(np.uint8)
    return compact, origin, [float(origin[0]), float(origin[1]), float(right - left), float(bottom - top)]


def _stable_child_bubble_id(
    page_key: str,
    parent_bubble_id: str,
    bbox: list[float],
    mask: np.ndarray,
) -> str:
    quantized_bbox = [round(float(value) / 4) * 4 for value in bbox]
    payload = (
        f"{page_key}|{parent_bubble_id}|{','.join(str(value) for value in quantized_bbox)}|"
        f"{_mask_fingerprint(mask)}"
    )
    digest = hashlib.blake2s(payload.encode("utf-8"), digest_size=6).hexdigest()
    return f"bubble-{digest}"


def _member_union_bbox(members: list[DetectionResult]) -> list[float]:
    left = min(region.bbox[0] for region in members)
    top = min(region.bbox[1] for region in members)
    right = max(region.bbox[0] + region.bbox[2] for region in members)
    bottom = max(region.bbox[1] + region.bbox[3] for region in members)
    return [left, top, right - left, bottom - top]


def _merge_balloon_group(group: list[tuple[int, DetectionResult]]) -> DetectionResult:
    if len(group) == 1:
        return group[0][1]

    indexed_members = sorted(group, key=lambda item: item[0])
    members = [region for _, region in indexed_members]
    bubble_ids = {region.bubble_id for region in members}
    if None in bubble_ids or len(bubble_ids) != 1:
        raise ValueError("Only text regions assigned to one bubble can be merged")
    bubble_id = next(iter(bubble_ids))

    bbox = _member_union_bbox(members)

    orientation_weights: dict[str, float] = {}
    for region in members:
        weight = max(1.0, region.bbox[2] * region.bbox[3]) * max(0.01, region.confidence)
        orientation_weights[region.orientation] = orientation_weights.get(region.orientation, 0.0) + weight
    orientation = max(orientation_weights, key=orientation_weights.get)

    if orientation == "vertical":
        reading_members = sorted(
            indexed_members,
            key=lambda item: (-(item[1].bbox[0] + item[1].bbox[2] / 2), item[1].bbox[1]),
        )
    else:
        reading_members = sorted(
            indexed_members,
            key=lambda item: (item[1].bbox[1] + item[1].bbox[3] / 2, item[1].bbox[0]),
        )
    source_index_by_original = {original_index: source_index for source_index, (original_index, _) in enumerate(indexed_members)}
    member_order = [source_index_by_original[original_index] for original_index, _ in reading_members]

    source_assignments = [
        region.metadata.get("balloon_assignment", {})
        if isinstance(region.metadata.get("balloon_assignment"), dict)
        else {}
        for region in members
    ]
    grouped_assignment: dict[str, object] = {"status": "assigned", "bubble_id": bubble_id}
    for key in ("coverage", "core_coverage", "score", "balloon_confidence"):
        values = [float(item[key]) for item in source_assignments if isinstance(item.get(key), (int, float))]
        if values:
            grouped_assignment[key] = round(min(values), 4)
    parent_ids = {
        str(item["parent_bubble_id"])
        for item in source_assignments
        if isinstance(item.get("parent_bubble_id"), str)
    }
    if len(parent_ids) == 1:
        grouped_assignment["parent_bubble_id"] = next(iter(parent_ids))

    metadata = dict(members[0].metadata)
    metadata["balloon_assignment"] = grouped_assignment
    line_grouping: dict[str, object] = {
        "method": "balloon_instance",
        "source_count": len(members),
        "source_boxes": [list(region.bbox) for region in members],
        "source_polygons": [[list(point) for point in region.polygon] for region in members],
        "source_confidences": [float(region.confidence) for region in members],
        "source_orientations": [region.orientation for region in members],
        "member_order": member_order,
    }
    if isinstance(metadata.get("instance_split"), dict):
        line_grouping["instance_split"] = metadata["instance_split"]
    metadata["line_grouping"] = line_grouping
    return DetectionResult(
        polygon=bbox_to_polygon(bbox),
        bbox=bbox,
        confidence=min(region.confidence for region in members),
        orientation=orientation,
        region_type=members[0].region_type,
        bubble_id=bubble_id,
        metadata=metadata,
        balloon_mask=members[0].balloon_mask,
        balloon_mask_origin=members[0].balloon_mask_origin,
        balloon_mask_id=members[0].balloon_mask_id,
        balloon_mask_parent_id=members[0].balloon_mask_parent_id,
        balloon_mask_confidence=members[0].balloon_mask_confidence,
    )


def group_text_lines(
    regions: list[DetectionResult],
    image_path: Path | None = None,
    *,
    max_background_residual: float = 16.0,
) -> list[DetectionResult]:
    """Merge co-oriented detector line boxes that form one visual text block."""
    if len(regions) < 2:
        return regions
    image = cv2.imread(str(image_path), cv2.IMREAD_COLOR) if image_path else None
    if image_path and image is None:
        raise ValueError(f"Cannot read grouping image: {image_path}")

    parents = list(range(len(regions)))

    def find(index: int) -> int:
        while parents[index] != index:
            parents[index] = parents[parents[index]]
            index = parents[index]
        return index

    def union(first: int, second: int) -> None:
        first_root, second_root = find(first), find(second)
        if first_root != second_root:
            parents[second_root] = first_root

    for first in range(len(regions)):
        for second in range(first + 1, len(regions)):
            if _same_text_block(regions[first], regions[second]) and (
                image is None
                or _background_residual(image, regions[first].bbox, regions[second].bbox)
                <= max_background_residual
            ):
                union(first, second)

    groups: dict[int, list[tuple[int, DetectionResult]]] = {}
    for index, region in enumerate(regions):
        groups.setdefault(find(index), []).append((index, region))

    merged = [_merge_group(group) for group in groups.values()]
    merged.sort(key=lambda item: item[0])
    return [item[1] for item in merged]


def _same_text_block(first: DetectionResult, second: DetectionResult) -> bool:
    if first.orientation != second.orientation:
        return False
    ax, ay, aw, ah = first.bbox
    bx, by, bw, bh = second.bbox
    if min(aw, ah, bw, bh) <= 0:
        return False

    if first.orientation == "vertical":
        thickness_ratio = max(aw, bw) / min(aw, bw)
        length_ratio = max(ah, bh) / min(ah, bh)
        column_gap = _interval_gap(ax, ax + aw, bx, bx + bw)
        length_overlap = _interval_overlap(ay, ay + ah, by, by + bh) / min(ah, bh)
        center_distance = abs((ay + ah / 2) - (by + bh / 2))
        return (
            thickness_ratio <= 2.0
            and length_ratio <= 2.2
            and column_gap <= max(12.0, max(aw, bw) * 0.75)
            and length_overlap >= 0.25
            and center_distance <= max(ah, bh) * 0.7
        )

    thickness_ratio = max(ah, bh) / min(ah, bh)
    row_gap = _interval_gap(ay, ay + ah, by, by + bh)
    length_overlap = _interval_overlap(ax, ax + aw, bx, bx + bw) / min(aw, bw)
    center_distance = abs((ax + aw / 2) - (bx + bw / 2))
    return (
        thickness_ratio <= 2.2
        and row_gap <= max(12.0, max(ah, bh) * 0.75)
        and length_overlap >= 0.25
        and center_distance <= max(aw, bw) * 0.7
    )


def _merge_group(group: list[tuple[int, DetectionResult]]) -> tuple[int, DetectionResult]:
    first_index = min(index for index, _ in group)
    if len(group) == 1:
        return first_index, group[0][1]
    members = [region for _, region in group]
    left = min(region.bbox[0] for region in members)
    top = min(region.bbox[1] for region in members)
    right = max(region.bbox[0] + region.bbox[2] for region in members)
    bottom = max(region.bbox[1] + region.bbox[3] for region in members)
    bbox = [left, top, right - left, bottom - top]
    source_boxes = [list(region.bbox) for _, region in sorted(group)]
    return first_index, DetectionResult(
        polygon=bbox_to_polygon(bbox),
        bbox=bbox,
        confidence=min(region.confidence for region in members),
        orientation=members[0].orientation,
        region_type=members[0].region_type,
        metadata={
            "line_grouping": {
                "method": "co_oriented_geometry",
                "source_count": len(members),
                "source_boxes": source_boxes,
            }
        },
    )


def _interval_gap(first_start: float, first_end: float, second_start: float, second_end: float) -> float:
    return max(0.0, max(first_start, second_start) - min(first_end, second_end))


def _interval_overlap(first_start: float, first_end: float, second_start: float, second_end: float) -> float:
    return max(0.0, min(first_end, second_end) - max(first_start, second_start))


def _background_residual(image: np.ndarray, first: list[float], second: list[float]) -> float:
    height, width = image.shape[:2]
    left = max(0, int(np.floor(min(first[0], second[0]))))
    top = max(0, int(np.floor(min(first[1], second[1]))))
    right = min(width, int(np.ceil(max(first[0] + first[2], second[0] + second[2]))))
    bottom = min(height, int(np.ceil(max(first[1] + first[3], second[1] + second[3]))))
    crop = image[top:bottom, left:right]
    if crop.size == 0:
        return float("inf")
    samples = cv2.cvtColor(crop, cv2.COLOR_BGR2LAB).reshape(-1, 3).astype(np.float32)
    cluster_count = max(1, min(4, len(samples) // 80))
    if cluster_count == 1:
        distances = np.linalg.norm(samples - np.median(samples, axis=0), axis=1)
    else:
        criteria = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 30, 0.2)
        _, labels, centers = cv2.kmeans(samples, cluster_count, None, criteria, 3, cv2.KMEANS_PP_CENTERS)
        distances = np.linalg.norm(samples - centers[labels.ravel()], axis=1)
    return float(np.percentile(distances, 80))
