"""Evidence checks for projection-profile text angles, without another model.

Uneven column lengths can sharpen a *global* diagonal projection even though
the individual lines are upright. Require a meaningful improvement over zero
and agreement along the text before accepting an automatic frame rotation.
"""
from __future__ import annotations

import math

import numpy as np

MIN_GLOBAL_GAIN = 1.10
MIN_BAND_GAIN = 1.03


def projection_score(projection: np.ndarray) -> float:
    projection = projection - math.floor(float(projection.min()))
    bins = projection.astype(np.int32)
    fraction = projection-bins
    histogram = np.bincount(bins, weights=1-fraction, minlength=int(bins.max())+2)
    histogram += np.bincount(bins+1, weights=fraction, minlength=len(histogram))
    return float(histogram@histogram)/len(projection)


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
