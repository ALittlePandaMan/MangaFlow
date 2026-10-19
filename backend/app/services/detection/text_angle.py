"""Evidence checks for projection-profile text angles, without another model.

Uneven column lengths can sharpen a *global* diagonal projection even though
the individual lines are upright. Require a meaningful improvement over zero
and agreement along the text before accepting an automatic frame rotation.
"""
from __future__ import annotations

import hashlib
import math
from collections import OrderedDict
from threading import Lock

import numpy as np

MIN_GLOBAL_GAIN = 1.10
MIN_BAND_GAIN = 1.03
ANGLE_PROJECTIONS = tuple((float(degrees), math.sin(math.radians(degrees)), math.cos(math.radians(degrees)))
                          for degrees in np.arange(-45., 45.01, .5))
# Cache only a digest and two scalars, never the model mask or page pixels.
_measurement_cache: OrderedDict[tuple, tuple[bool, float]] = OrderedDict()
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


def measure_text_mask(window: np.ndarray, vertical: bool) -> tuple[bool, float]:
    """The same exhaustive search, memoized across repeat detections of a page.

    Include the exact cropped foreground and fallback direction in the key;
    changing a single mask pixel or crop invalidates the result. User padding,
    positioning and repair masks never enter this cache.
    """
    digest = hashlib.sha256(np.packbits(window != 0).tobytes()).digest()
    key = window.shape, digest, vertical
    with _measurement_lock:
        cached = _measurement_cache.get(key)
        if cached is not None:
            _measurement_cache.move_to_end(key)
            return cached
    rows, columns = np.nonzero(window)
    angle = 0.
    if len(rows) >= 4:
        stride = max(1, math.ceil(len(rows)/8192))
        x, y = columns[::stride].astype(float), rows[::stride].astype(float)
        scores = [(0., 0.), (0., 0.)]
        for degrees, sin, cos in ANGLE_PROJECTIONS:
            for axis, projection in enumerate((y*cos-x*sin, x*cos+y*sin)):
                score = projection_score(projection)
                if score > scores[axis][0]:
                    scores[axis] = score, degrees
        maximum = max(scores[0][0], scores[1][0])
        if abs(scores[0][0]-scores[1][0]) > maximum*.02:
            vertical = scores[1][0] > scores[0][0]
        angle = supported_text_angle(x, y, vertical, scores[int(vertical)][1])
    result = vertical, angle
    with _measurement_lock:
        _measurement_cache[key] = result
        _measurement_cache.move_to_end(key)
        if len(_measurement_cache) > _MEASUREMENT_CACHE_SIZE:
            _measurement_cache.popitem(last=False)
    return result


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

    along = y if vertical else x
    edges = np.linspace(float(along.min()), float(along.max())+1, 4)
    gains = []
    for low, high in zip(edges, edges[1:], strict=False):
        selected = (along >= low) & (along < high)
        # A few dots/pixels must not count as independent evidence of a line.
        if np.count_nonzero(selected) < 12:
            continue
        gains.append(projection_score(tilted[selected])/projection_score(upright[selected]))
    if sum(gain >= MIN_BAND_GAIN for gain in gains) < 2:
        return 0.
    if any(gain < .90 for gain in gains):
        return 0.  # One section strongly contradicts the apparent global tilt.
    return angle
