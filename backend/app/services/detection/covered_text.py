"""Lettering-backed containment resolution for dialogue and free text.

Balloon/panel ownership limits the candidate set, not the decision or thresholds.
A complete query may replace a redundant fragment; complete leaf crops may
replace an enclosing parent. Prose is recovered first from all original ink.
IoU supplements lettering evidence for near-identical queries, never replaces
the containment/component proofs. Missing or ambiguous evidence stays intact.
"""
from __future__ import annotations

import hashlib
from collections import Counter

import numpy as np
from app.services.detection.mask_evidence import (
    _box_coverage,
    _box_iou,
    _coverage,
    _dilate,
    _Mask,
    _union_masks,
    _window,
)
from app.services.detection.mask_geometry import components_supported, covered_components
from app.services.detection.text_roles import LayoutPrediction


def _frames_cover(mask: _Mask, polygons: list[list[list[float]]]) -> bool:
    """Every foreground pixel centre must lie in a retained convex frame.

    Test the actual padded/rotated polygons, not their enclosing rectangle or
    the bounding rectangle of the whole group. Chunking bounds temporary memory.
    """
    frames = []
    for polygon in polygons:
        points = np.asarray(polygon, dtype=float)
        if points.shape != (4, 2) or not np.isfinite(points).all():
            return False
        edges = np.roll(points, -1, axis=0)-points
        area = float(np.sum(points[:, 0]*np.roll(points[:, 1], -1)-points[:, 1]*np.roll(points[:, 0], -1)))
        if abs(area) < 1e-6:
            return False
        frames.append((points, edges, 1 if area > 0 else -1))
    for start in range(0, mask.pixels.shape[0], 128):
        rows, cols = np.nonzero(mask.pixels[start:start+128])
        x, y = cols+mask.x+.5, rows+mask.y+start+.5
        covered = np.zeros(len(rows), dtype=bool)
        for points, edges, sign in frames:
            inside = np.ones(len(rows), dtype=bool)
            for (px, py), (dx, dy) in zip(points, edges, strict=True):
                inside &= sign*(dx*(y-py)-dy*(x-px)) >= -1e-6
            covered |= inside
        if not covered.all():
            return False
    return bool(np.count_nonzero(mask.pixels))


def duplicate_text_instances(
    predictions: list[LayoutPrediction],
    bubble_indices: dict[int, int | None],
    geometries: dict[int, tuple[list[list[float]], str, float]],
    panels: dict[int, int | None],
    *,
    bubble_coverage: dict[int, float],
    include_fragments: bool = True,
    replacements: dict[int, int] | None = None,
    iou_replacements: dict[int, int] | None = None,
) -> set[int]:
    """Resolve duplicates with complementary geometry AND lettering proofs.

    A larger complete query may replace a contained fragment, but never consume
    independent sibling blocks. If the smaller crop contains ALL parent ink,
    defer to complete-parent resolution so the tighter crop can survive.
    Every removal is justified against a surviving crop, never a deleted query.
    Optional replacements records removed -> survivor for later crop proofs.
    """
    kept, removed = [], set()
    masks: dict[int, _Mask] = {}
    expanded: dict[int, _Mask] = {}

    def mask(i):
        if i not in masks:
            masks[i] = _window(predictions[i])
        return masks[i]

    def duplicate(index, winner):
        first, second = predictions[index], predictions[winner]
        a, b = first.bbox[2]*first.bbox[3], second.bbox[2]*second.bbox[3]
        if max(_box_coverage(first, second), _box_coverage(second, first)) < .8:
            return False
        if (bubble_indices.get(index) != bubble_indices.get(winner)
                or panels.get(index) != panels.get(winner)):
            return False
        own, other = mask(index), mask(winner)
        # Exact same crop/evidence remains safe even if segmentation is absent.
        # Never use empty/solid masks to justify suppressing shifted geometry.
        if (first.bbox == second.bbox and own.x == other.x and own.y == other.y
                and np.array_equal(own.pixels, other.pixels)):
            return True
        if bubble_indices.get(index) is not None and any(bubble_coverage.get(i, 0.) < .95 for i in (index, winner)):
            return False
        if (geometries[index][1] != geometries[winner][1]
                or abs(geometries[index][2]-geometries[winner][2]) > 10):
            return False
        if any(not m.pixels.size or not 0 < float(m.pixels.mean()) < .8 for m in (own, other)):
            return False
        for i in (index, winner):
            if i not in expanded:
                expanded[i] = _dilate(mask(i))
        matching = all(
            _coverage(mask(i), [mask(j)]) >= .9 and _coverage(mask(i), [expanded[j]]) >= .98
            for i, j in ((index, winner), (winner, index)))
        # RF-DETR can produce slightly different contours/thickness for the
        # same high-overlap query. Raw-mask equality is too strict there. IoU
        # only opens this alternative when EACH original glyph component is
        # supported by the other query (1px transient tolerance, not a changed
        # repair mask), and both crops will still pass complete-frame coverage.
        iou_match = (include_fragments and _box_iou(first, second) >= .75
            and all(_coverage(mask(i), [expanded[j]]) >= .93
                    and components_supported(mask(i), expanded[j])
                    for i, j in ((index, winner), (winner, index))))
        iou_rescue = iou_match and not matching
        matching |= iou_match
        if matching and not include_fragments:
            # This pass precedes prose reconstruction: only identical ink may
            # disappear before the full-line evidence is collected. Approximate
            # matches are handled later, with replacement provenance retained.
            matching = all(_coverage(mask(i), [mask(j)]) == 1.
                           for i, j in ((index, winner), (winner, index)))
        contained = (not iou_rescue and include_fragments and a < b and _box_coverage(first, second) >= .8
            and _coverage(own, [other]) >= .9 and _coverage(own, [expanded[winner]]) >= .98)
        if (matching or contained) and a < b and any(
            i not in {index, winner}
            and predictions[i].bbox[2]*predictions[i].bbox[3] < b
            and _box_coverage(predictions[i], second) >= .8
            and max(_box_coverage(predictions[i], first), _box_coverage(first, predictions[i])) <= .2
            for i in geometries
        ):
            return False  # Preserve independently detected phrases on one line/column.
        if not matching and not contained:
            return False
        if contained:
            if _frames_cover(other, [geometries[index][0]]):
                return False  # All parent ink fits the tighter crop: prefer it later.
            own_bubble, own_panel = bubble_indices.get(winner), panels.get(winner)
            if any(item.label in {"bubble", "panel", "onomatopoeia"}
                   and i not in {own_bubble, own_panel}
                   and max(_box_coverage(item, second), _box_coverage(second, item)) > .05
                   for i, item in enumerate(predictions)):
                return False  # A broad crop must not erase another layout context.
        # The removed fragment must fit ONE surviving crop, not a union. Its
        # unique punctuation outside that crop always protects it. A contained
        # fragment need not explain the larger query's independent edge pixels:
        # that query and its crop are retained unchanged, not replaced here.
        required = (own,) if contained else (own, other)
        complete = all(_frames_cover(m, [geometries[winner][0]]) for m in required)
        if complete and iou_match and iou_replacements is not None:
            iou_replacements[index] = winner
        return complete

    # Identical ink prefers a tighter crop; partial fragments prefer the larger
    # complete query. Neither decision is dictated by confidence alone.
    area_order = -1 if include_fragments else 1
    order_keys = {i: (area_order*predictions[i].bbox[2]*predictions[i].bbox[3],
                     -predictions[i].confidence, *predictions[i].bbox) for i in geometries}
    ties = Counter(order_keys.values())
    # Equal boxes/confidences with slightly different segmentation must not
    # choose different repair evidence just because worker query order changed.
    for i, key in order_keys.items():
        if ties[key] > 1:
            own = mask(i)
            order_keys[i] = (*key, own.x, own.y, *own.pixels.shape,
                             hashlib.sha256(own.pixels.tobytes()).digest())
    for index in sorted(geometries, key=order_keys.__getitem__):
        winner = next((winner for winner in kept if duplicate(index, winner)), None)
        if winner is not None:
            removed.add(index)
            if replacements is not None:
                replacements[index] = winner
        else:
            kept.append(index)
    return removed


def covered_text_parents(
    predictions: list[LayoutPrediction],
    bubble_indices: dict[int, int | None],
    geometries: dict[int, tuple[list[list[float]], str, float]],
    panels: dict[int, int | None],
    *,
    reserved: set[int],
    bubble_coverage: dict[int, float],
    ignored: set[int] | None = None,
    replacements: dict[int, int] | None = None,
) -> dict[int, list[int]]:
    """Return removable parent -> retained children, without mutating inputs.

    Resolve inner containers before outer ones. Every original mask follows its
    surviving crops, so another removal cannot lose a previously rescued stroke.
    Reserved prose queries cannot be repurposed. Unproven overlap stays intact.
    """
    def area(i: int) -> float:
        return predictions[i].bbox[2]*predictions[i].bbox[3]

    def overlap(a: int, b: int) -> float:
        return max(_box_coverage(predictions[a], predictions[b]), _box_coverage(predictions[b], predictions[a]))

    def key(i: int) -> tuple:
        return -area(i), *predictions[i].bbox, -predictions[i].confidence

    ignored = ignored or set()
    eligible = {i for i in geometries if i not in reserved and i not in ignored
                and (bubble_indices.get(i) is None or bubble_coverage.get(i, 0.) >= .95)}
    masks: dict[int, _Mask] = {}
    expanded: dict[int, _Mask] = {}
    groups: dict[int, list[int]] = {}
    provenance: dict[int, set[int]] = {}
    for removed, owner in (replacements or {}).items():
        provenance.setdefault(owner, set()).add(removed)

    def prepare(i: int) -> bool:
        if i not in masks:
            masks[i] = _window(predictions[i])
        pixels = masks[i].pixels
        if not pixels.size or not 0 < float(pixels.mean()) < .8:
            return False  # Empty/filled segmentation isn't evidence of lettering.
        if i not in expanded:
            expanded[i] = _dilate(masks[i])
        return True

    for parent in sorted(eligible, key=lambda i: (area(i), *predictions[i].bbox, -predictions[i].confidence)):
        # Ownership is the only context-specific guard. Unknown balloon
        # assignments must not fall through as if they were proven free text.
        own_bubble, own_panel = bubble_indices.get(parent), panels.get(parent)
        if any(item.label in {"bubble", "panel"} and overlap(parent, i) > .05
               for i, item in enumerate(predictions) if i not in {own_panel, own_bubble}):
            continue
        children = sorted((i for i in geometries if i != parent and i not in ignored and i not in groups and area(i) < area(parent)
                           and _box_coverage(predictions[i], predictions[parent]) >= .8), key=key)
        if not children or any(i not in eligible or panels.get(i) != own_panel
                               or bubble_indices.get(i) != own_bubble for i in children):
            continue
        # Only independent surviving crops qualify. A nested container is absent
        # here ONLY after its own complete-ink proof has already succeeded.
        if any(overlap(a, b) > .2 for offset, a in enumerate(children) for b in children[offset+1:]):
            continue
        child_set = set(children)
        # A compatible outer candidate must not block resolving this inner
        # container. It is NOT removed here and must pass its own proof later.
        ancestors = {i for i in eligible if area(i) > area(parent)
                     and panels.get(i) == own_panel and bubble_indices.get(i) == own_bubble
                     and _box_coverage(predictions[parent], predictions[i]) >= .8}
        resolved = {i for i, leaves in groups.items() if set(leaves) <= child_set}
        if any(item.label in {"text", "onomatopoeia"} and overlap(parent, i) > .1
               for i, item in enumerate(predictions)
               if i != parent and i not in child_set and i not in ignored and i not in ancestors and i not in resolved):
            continue
        direction, angle = geometries[children[0]][1:]
        if any(geometries[i][1] != direction or abs(geometries[i][2]-angle) > 10 for i in children):
            continue
        # The aggregate parent's direction is deliberately NOT used: combining
        # two horizontal blocks can make its shape appear vertical.
        if not all(prepare(i) for i in [parent, *children]):
            continue
        if (_coverage(masks[parent], [masks[i] for i in children]) < .85
                or _coverage(masks[parent], [expanded[i] for i in children]) < .98):
            continue
        strict_agreement = all(_coverage(masks[i], [masks[parent]]) >= .9
                               and _coverage(masks[i], [expanded[parent]]) >= .98 for i in children)
        # Parent and leaf queries can disagree on stroke thickness. Permit that
        # ONLY with support for every child component, the unchanged parent
        # agreement above, and the complete original-component crop proof below.
        if not strict_agreement and not all(components_supported(masks[i], expanded[parent]) for i in children):
            continue  # No fallback for a wholly unsupported character/mark.
        # A 99% aggregate score can still miss a whole punctuation mark. All
        # parent foreground must survive in the final child crops, no exceptions.
        frames = [geometries[i][0] for i in children]
        # A glyph split between two children passes union coverage, but neither
        # OCR crop contains the complete glyph. Check BOTH representations:
        # parent/child segmentations can each include unique edge strokes.
        # A retained parent may have replaced a fragment whose mask had unique
        # edge pixels. Carry that ORIGINAL ink through any subsequent parent
        # replacement, rather than silently trusting only its representative.
        evidence = {parent, *children}
        for i in [parent, *children]:
            evidence.update(provenance.get(i, ()))
        if not all(prepare(i) for i in evidence):
            continue
        # Individual partial masks can each look disconnected even though their
        # union completes one glyph across two crops. Validate that whole union
        # once, so a component cannot disappear between separate query checks.
        ink = _union_masks([masks[i] for i in sorted(evidence)])
        if not covered_components(ink, frames)[ink.pixels > 0].all():
            continue
        groups[parent] = children
        for child in children:
            provenance.setdefault(child, set()).update(evidence)
    return groups
