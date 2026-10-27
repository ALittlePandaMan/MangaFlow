"""Read-only image-evidence planning, shared with workflow admission checks."""
from datetime import timedelta
from math import ceil

from app.api.helpers import require_page
from app.core.database import SessionLocal
from app.models import MCPAsset
from app.models.entities import utcnow
from app.services.workflow_state import current_page_version
from fastapi import HTTPException
from PIL import Image

MAX_EVIDENCE = 256
READABLE_SCALE = 2
REQUIRED_COVERAGE = .98


def clean_preview_path(page):
    """Original pixels are the clean background only when no erasure exists."""
    from app.services.regions import STALE_CLEAN_PATH_KEY
    from app.storage import get_storage
    if page.clean_path:
        return page.clean_path
    if (page.metadata_json or {}).get(STALE_CLEAN_PATH_KEY):
        return None
    for row in page.regions:
        if row.inpainted_path:
            return None
        if row.pixel_mask_path:
            path = get_storage().absolute(row.pixel_mask_path)
            if not path.is_file():
                return None
            with Image.open(path) as mask:
                if mask.convert("L").getbbox():
                    return None
    return page.original_path


def covered_area(rectangles):
    edges = sorted({v for x, _, w, _ in rectangles for v in (x, x+w)})
    total = 0
    for left, right in zip(edges, edges[1:], strict=False):
        intervals = sorted((y, y+h) for x, y, w, h in rectangles if x <= left and x+w >= right)
        end, height = 0, 0
        for start, stop in intervals:
            height += max(0, stop-max(start, end))
            end = max(end, stop)
        total += (right-left)*height
    return total


def load_evidence(db, identifiers, version, page):
    result = []
    for identifier in identifiers:
        asset = db.get(MCPAsset, identifier)
        value = asset.evidence if asset else {}
        if (not value or value.get("image_id") != page.id or value.get("version") != version
                or asset.status != "AVAILABLE" or asset.created_at < utcnow()-timedelta(hours=24)):
            raise HTTPException(409, "Preview evidence expired, is missing or belongs to another page/version; fetch fresh previews")
        result.append({"id": identifier, **value})
    return result


def requirements(phase):
    return (["annotated"] if phase == "preflight" else ["original", "rendered"],
            "source" if phase == "preflight" else "translation",
            ["original"] if phase == "preflight" else ["original", "rendered"])


def evidence_views(evidence, kind):
    """Normalize only server-persisted images/panels, never client assertions."""
    result = []
    for value in evidence:
        if value["kind"] == "comparison":
            result.extend(panel for panel in value.get("panels", []) if panel["kind"] == kind)
        elif value["kind"] == kind or kind == "original" and value["kind"] == "annotated":
            result.append(value)
    return result


def readable_rectangles(evidence, kind):
    return [value["crop"] for value in evidence_views(evidence, kind)
            if max(value["original_pixels_per_display_pixel"]) <= READABLE_SCALE]


def review_plan(args):
    """Return page_preview calls, never create previews or perform a review."""
    with SessionLocal() as db:
        page = require_page(db, args["image_id"])
        version = current_page_version(db, page.id)
        if args["expected_version"] != version:
            raise HTTPException(409, "Page changed; refresh page_state before planning review images")
        evidence = load_evidence(db, args.get("evidence_ids", []), version, page)
        phase = args["phase"]
        kinds, frame, readable_kinds = requirements(phase)
        full = [0, 0, page.width, page.height]
        requests, blockers, coverage = [], [], {}
        unavailable = set()
        if phase == "final":
            if not page.rendered_path:
                unavailable.add("rendered")
            if not clean_preview_path(page):
                unavailable.add("clean")
        for kind in sorted(unavailable):
            blockers.append({"code": "preview_unavailable", "kind": kind,
                "message": "Complete the required processing stage, then refresh the page version."})

        def request(kind, crop, reason):
            requests.append({"tool": "page_preview", "arguments": {"image_id": page.id, "kind": kind,
                "frame": frame, "crop": crop, "max_size": 2048 if kind == "comparison" else 1600,
                "expected_version": version}, "reason": reason})

        overview_reason = "Inspect the complete page, including areas outside detected regions."
        if phase == "preflight":
            if not any(value["kind"] == "annotated" and value.get("frame") == frame and value["crop"] == full for value in evidence):
                request("annotated", full, overview_reason)
        else:
            missing = [kind for kind in readable_kinds if kind not in unavailable
                and not any(value["crop"] == full for value in evidence_views(evidence, kind))]
            if len(missing) == 2:
                request("comparison", full, overview_reason)
            elif missing:
                request(missing[0], full, overview_reason)
        for kind in readable_kinds:
            if kind in unavailable:
                continue
            matching = evidence_views(evidence, kind)
            rectangles = readable_rectangles(evidence, kind)
            coverage[kind] = covered_area(rectangles)/(page.width*page.height)
            if coverage[kind] >= REQUIRED_COVERAGE:
                continue
            # First inspect the full image. Its actual compression/scale decides
            # whether any tiles are needed, rather than predicting PNG sizes.
            if not any(value["crop"] == full for value in matching):
                continue
            failed = [value for value in matching if max(value["original_pixels_per_display_pixel"]) > READABLE_SCALE]
            side = min([1600, *[max(1, ceil(max(value["crop"][2:])/2)) for value in failed]])
            step = max(1, side-side//10)
            columns = max(1, ceil(max(0, page.width-side)/step)+1)
            rows = max(1, ceil(max(0, page.height-side)/step)+1)
            if columns*rows > MAX_EVIDENCE:
                blockers.append({"code": "evidence_limit", "kind": kind,
                    "message": "Readable whole-page coverage exceeds the evidence limit. Report visual review as unverified; do not use client-side image processing."})
                continue
            for top in range(rows):
                for left in range(columns):
                    x, y = min(left*step, max(0, page.width-side)), min(top*step, max(0, page.height-side))
                    crop = [x, y, min(side, page.width-x), min(side, page.height-y)]
                    clipped = [[max(x, rx), max(y, ry), min(x+crop[2], rx+rw)-max(x, rx),
                        min(y+crop[3], ry+rh)-max(y, ry)] for rx, ry, rw, rh in rectangles
                        if min(x+crop[2], rx+rw) > max(x, rx) and min(y+crop[3], ry+rh) > max(y, ry)]
                    if covered_area(clipped) < crop[2]*crop[3]:
                        request(kind, crop, "Inspect readable overlapping tiles; image crops are generated by MangaFlow.")
        # Share a crop only when both images are missing at that location.
        # If one side is already readable, do not send its pixels again.
        if phase == "final":
            merged = {}
            for item in requests:
                key = tuple(item["arguments"]["crop"])
                previous = merged.get(key)
                if previous and {previous["arguments"]["kind"], item["arguments"]["kind"]} == {"original", "rendered"}:
                    previous["arguments"].update(kind="comparison", max_size=2048)
                else:
                    merged[key] = item
            requests = list(merged.values())
        if len(evidence)+len(requests) > MAX_EVIDENCE:
            blockers.append({"code": "evidence_limit", "message": "More previews would exceed 256 evidence IDs. Re-plan with only the required, readable current-version evidence; otherwise report unverified."})
        if any(item["code"] == "evidence_limit" for item in blockers):
            requests = []  # Never suggest a sequence that cannot satisfy admission.
        offset, limit = args.get("offset", 0), args.get("limit", 12)
        if current_page_version(db, page.id) != version:
            raise HTTPException(409, "Page changed while planning; refresh page_state")
        return {"image_id": page.id, "version": version, "phase": phase, "frame": frame,
            "required_kinds": kinds, "readable_coverage": coverage, "required_coverage": REQUIRED_COVERAGE,
            "max_original_pixels_per_display_pixel": READABLE_SCALE, "max_evidence": MAX_EVIDENCE,
            "requests": requests[offset:offset+limit], "request_count": len(requests),
            "next_offset": offset+limit if offset+limit < len(requests) else None, "blockers": blockers,
            "evidence_ready": not requests and not blockers, "visual_review_performed": False,
            "next_instruction": "View each returned native image. Re-run this plan with its preview_id; only then submit your own visual judgment. Keep evidence_ids unchanged while following next_offset."}
