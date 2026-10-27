"""Evidence checks for projection-profile text angles, without another model.

Uneven column lengths can sharpen a *global* diagonal projection even though
the individual lines are upright. Require a meaningful improvement over zero
and agreement along the text before accepting an automatic frame rotation.
"""
from __future__ import annotations

import hashlib
import math
from collections import OrderedDict
from copy import deepcopy
from threading import Lock

import numpy as np
from app.core.timing import measured

MIN_GLOBAL_GAIN = 1.10
MIN_BAND_GAIN = 1.03
TEXT_ANGLE_VERSION = 2
# Cache only digests and scalar evidence, never the model mask or page pixels.
_measurement_cache: OrderedDict[tuple, dict] = OrderedDict()
_measurement_lock = Lock()
_MEASUREMENT_CACHE_SIZE = 256


def projection_score(projection: np.ndarray) -> float:
    projection = projection - math.floor(float(projection.min()))
    bins = projection.astype(np.int32)
    fraction = projection-bins
    histogram = np.bincount(bins, weights=1-fraction, minlength=int(bins.max())+2)
    # Identical bins/summation order without allocating bins+1 for every angle.
    histogram[1:] += np.bincount(bins, weights=fraction, minlength=len(histogram)-1)
    return float(histogram@histogram)/len(projection)


@measured("typography.angle")
def analyze_text_mask(window: np.ndarray, vertical: bool, *, fixed_direction: bool = False) -> dict:
    """Bounded, shared direction/angle evidence; never infer tilt from a box."""
    digest = hashlib.sha256(np.packbits(window != 0).tobytes()).digest()
    key = window.shape, digest, vertical, fixed_direction, TEXT_ANGLE_VERSION
    with _measurement_lock:
        cached = _measurement_cache.get(key)
        if cached is not None:
            _measurement_cache.move_to_end(key)
            return deepcopy(cached)
    # Bound temporary coordinate arrays as well as the final projection sample.
    stride = max(1, math.ceil(math.sqrt(window.size / 1_000_000)))
    rows, columns = np.nonzero(window[::stride, ::stride])
    result = {"version": TEXT_ANGLE_VERSION, "fingerprint": hashlib.sha256(repr(key).encode()).hexdigest(),
              "orientation": "vertical" if vertical else "horizontal", "orientation_confident": False,
              "angle": 0., "status": "uncertain", "reason": "insufficient_foreground"}
    if len(rows) >= 4:
        sample = max(1, math.ceil(len(rows)/8192))
        x, y = columns[::sample].astype(float)*stride, rows[::sample].astype(float)*stride
        # Histograms must be bounded too, even for an unusually long strip.
        scale = max(1., float(max(np.ptp(x), np.ptp(y)))/8192)
        x, y = x/scale, y/scale
        searches = [_angle_search(x, y, axis) for axis in (False, True)]
        scores = [max(values, key=lambda item: (item[0], -abs(item[1]))) for values in searches]
        maximum = max(scores[0][0], scores[1][0])
        certain_direction = abs(scores[0][0]-scores[1][0]) > maximum*.05
        if certain_direction and not fixed_direction:
            vertical = scores[1][0] > scores[0][0]
        best, candidate = scores[int(vertical)]
        angle = supported_text_angle(x, y, vertical, candidate)
        competing = any(score >= best*.98 and abs(degrees-candidate) >= 5
                        for score, degrees in searches[int(vertical)])
        reason = "upright" if abs(candidate) < 3 else "inconsistent_sections"
        if angle and competing:
            angle, reason = 0., "competing_angles"
        if angle and abs(candidate) >= 43 and not certain_direction:
            angle, reason = 0., "ambiguous_direction_boundary"
        result.update(orientation="vertical" if vertical else "horizontal",
                      orientation_confident=certain_direction and not fixed_direction, angle=angle,
                      status="accepted" if angle else "upright" if abs(candidate) < 3 else "uncertain",
                      reason="consistent_text_lines" if angle else reason)
        # One connected letter or an outline is not an independent text line.
        # In particular, an italic glyph must not turn a horizontal frame into
        # vertical writing merely because its strokes have a stronger profile.
        import cv2
        count, _, stats, _ = cv2.connectedComponentsWithStats((window[::stride, ::stride] != 0).astype(np.uint8))
        if sum(stats[index, cv2.CC_STAT_AREA] >= 4 for index in range(1, count)) < 2:
            result.update(orientation="vertical" if key[2] else "horizontal", orientation_confident=False,
                          angle=0., status="uncertain", reason="insufficient_independent_text")
    with _measurement_lock:
        _measurement_cache[key] = result
        _measurement_cache.move_to_end(key)
        if len(_measurement_cache) > _MEASUREMENT_CACHE_SIZE:
            _measurement_cache.popitem(last=False)
    return deepcopy(result)


def measure_text_mask(window: np.ndarray, vertical: bool) -> tuple[bool, float]:
    result = analyze_text_mask(window, vertical)
    return result["orientation"] == "vertical", result["angle"]


def _score(x, y, vertical, degrees):
    rad = math.radians(degrees)
    return projection_score(x*math.cos(rad)+y*math.sin(rad) if vertical else y*math.cos(rad)-x*math.sin(rad))


def _angle_search(x, y, vertical):
    coarse = [(_score(x, y, vertical, float(a)), float(a)) for a in range(-45, 46, 3)]
    peaks = sorted(coarse, reverse=True)[:3]
    angles = {0., *(float(a) for a in range(-45, 46, 3))}
    for _, candidate in peaks:
        angles.update(float(a) for a in np.arange(max(-45., candidate-3), min(45., candidate+3)+.1, .5))
    known = {angle: score for score, angle in coarse}
    return [(known[a] if a in known else _score(x, y, vertical, a), a) for a in sorted(angles)]


def supported_text_angle(x: np.ndarray, y: np.ndarray, vertical: bool, angle: float) -> float:
    """Keep the candidate only if at least two local sections support it.

    This is a confidence gate, not a second angle search. Tiny punctuation-only
    samples and ambiguous handwriting default to upright; user rotation and
    manually adjusted geometry are outside this detector-only code path.
    """
    if abs(angle) < 3 or len(x) < 24:
        return 0.
    rad = math.radians(angle)
    sin, cos = math.sin(rad), math.cos(rad)
    upright = x if vertical else y
    tilted = x*cos+y*sin if vertical else y*cos-x*sin
    if projection_score(tilted) < MIN_GLOBAL_GAIN*projection_score(upright):
        return 0.

    # Sections follow the candidate's reading axis, including oblique lines.
    along = y*cos-x*sin if vertical else x*cos+y*sin
    edges = np.linspace(float(along.min()), float(along.max())+1, 4)
    gains = []
    section_sizes = []
    agreeing = 0
    for low, high in zip(edges, edges[1:], strict=False):
        selected = (along >= low) & (along < high)
        # A few dots/pixels must not count as independent evidence of a line.
        if np.count_nonzero(selected) < 12:
            continue
        gains.append(projection_score(tilted[selected])/projection_score(upright[selected]))
        section_sizes.append(np.count_nonzero(selected))
        # Search independently; looking only near the global angle would make
        # the agreement check tautological on irregular handwritten columns.
        local = max(((_score(x[selected], y[selected], vertical, float(a)), float(a))
                     for a in {*range(-45, 46, 3), angle-1, angle, angle+1, 0.}),
                    key=lambda item: (item[0], -abs(item[1])))
        agreeing += (abs(local[1]-angle) <= 3 or projection_score(tilted[selected]) >= local[0]*.97) and gains[-1] >= MIN_BAND_GAIN
    if sum(gain >= MIN_BAND_GAIN for gain in gains) < 2:
        return 0.
    if any(gain < .90 and size >= len(x)*.2 for gain, size in zip(gains, section_sizes, strict=True)):
        return 0.  # One section strongly contradicts the apparent global tilt.
    if agreeing < 2:
        return 0.
    return angle
