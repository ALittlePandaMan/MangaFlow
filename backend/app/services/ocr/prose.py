"""Native-only OCR chunks bound to unchanged, verified prose geometry."""
import math

from app.utils.geometry import bbox_to_polygon


def native_boxes(region, orientation, padding=0):
    detection = (region.layout_data or {}).get("detection") or {}
    metadata = detection.get("long_text_partition") or {}
    if (detection.get("ocr_frame_bbox") != region.bbox or metadata.get("direction") != orientation
            or region.polygon != bbox_to_polygon(region.bbox)):
        return [list(region.bbox)]
    boxes = metadata.get("ocr_boxes")
    if not isinstance(boxes, list) or not 1 < len(boxes) <= 80:
        return [list(region.bbox)]
    x,y,w,h = region.bbox
    if any(not isinstance(box, list) or len(box) != 4
           or any(not isinstance(v, (int,float)) or not math.isfinite(v) for v in box)
           or box[2] <= 0 or box[3] <= 0 or box[0] < x or box[1] < y
           or box[0]+box[2] > x+w+1e-6 or box[1]+box[3] > y+h+1e-6 for box in boxes):
        return [list(region.bbox)]
    # Native subcrops must not pick up the next line when the normal OCR
    # padding is wider than the line gap. Divide available gaps; the actual
    # recognition call then uses zero additional padding for these jobs.
    padding = max(0, int(padding))
    expanded = []
    for box in boxes:
        bx,by,bw,bh = box
        left,top,right,bottom = max(x,bx-padding),max(y,by-padding),min(x+w,bx+bw+padding),min(y+h,by+bh+padding)
        for ox,oy,ow,oh in boxes:
            if orientation == 'horizontal':
                if oy+oh <= by:
                    top = max(top,(oy+oh+by)/2)
                if oy >= by+bh:
                    bottom = min(bottom,(oy+by+bh)/2)
            else:
                if ox+ow <= bx:
                    left = max(left,(ox+ow+bx)/2)
                if ox >= bx+bw:
                    right = min(right,(ox+bx+bw)/2)
        expanded.append([math.floor(left), math.floor(top), math.ceil(right)-math.floor(left), math.ceil(bottom)-math.floor(top)])
    return expanded
