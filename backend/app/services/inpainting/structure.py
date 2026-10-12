"""Conservative veto for retry damage to lines anchored outside the text mask."""
import cv2
import numpy as np


def retry_structure_loss(initial: np.ndarray, candidate: np.ndarray, retry: np.ndarray) -> dict:
    if initial.shape != candidate.shape or initial.shape[:2] != retry.shape:
        raise ValueError("Structure comparison shape mismatch")
    x, y, w, h = cv2.boundingRect(retry)
    if not w or not h:
        return {"damaged": False, "protected_pixels": 0, "lost_pixels": 0}
    x1, y1 = max(0, x - 32), max(0, y - 32)
    roi = np.s_[y1:min(retry.shape[0], y+h+32), x1:min(retry.shape[1], x+w+32)]
    old = cv2.Canny(cv2.cvtColor(initial[roi], cv2.COLOR_RGB2GRAY), 60, 140)
    new = cv2.Canny(cv2.cvtColor(candidate[roi], cv2.COLOR_RGB2GRAY), 60, 140)
    area = retry[roi] > 0
    lines = cv2.HoughLinesP(old, 1, np.pi / 180, threshold=16, minLineLength=24, maxLineGap=4)
    protected = np.zeros(old.shape, np.uint8)
    if lines is not None:
        # Longest edges first; bounded even on screentone-heavy pages.
        ordered = sorted(lines[:, 0], key=lambda p: -float(np.hypot(p[2]-p[0], p[3]-p[1])))
        for ax, ay, bx, by in ordered[:256]:
            n = max(abs(int(bx-ax)), abs(int(by-ay))) + 1
            xs = np.rint(np.linspace(ax, bx, n)).astype(int)
            ys = np.rint(np.linspace(ay, by, n)).astype(int)
            inside = area[ys, xs]
            supported = old[ys, xs] > 0
            # Text strokes contained in the mask are not protected artwork.
            if np.count_nonzero(inside & supported) < 4 or np.count_nonzero(~inside & supported) < 12:
                continue
            protected[ys[inside & supported], xs[inside & supported]] = 255
    near_new = cv2.dilate(new, np.ones((3, 3), np.uint8))
    total = cv2.countNonZero(protected)
    lost = int(np.count_nonzero((protected > 0) & (near_new == 0)))
    return {"damaged": lost >= max(4, int(np.ceil(total * .15))),
            "protected_pixels": total, "lost_pixels": lost}
