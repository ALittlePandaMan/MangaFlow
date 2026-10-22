"""Native OCR partial results, scoped to the immutable page-stage lineage."""
from __future__ import annotations

from app.services.base import OCRResult
from app.services.cloud_results import current_cloud_results
from app.services.ocr.crops import OCR_CROP_VERSION
from app.services.workflow_state import fingerprint


def recognize_local(provider,source,regions,padding,*,polygons=None,crop_angles=None,batch=False,check=None):
    if polygons is not None and len(polygons) != len(regions):
        raise ValueError("OCR polygon count mismatch")
    if crop_angles is not None and len(crop_angles) != len(regions):
        raise ValueError("OCR angle count mismatch")

    def infer(jobs, outlines, angles):
        if batch:
            return provider.recognize_batch(source,jobs,padding,check=check,
                **({"polygons": outlines} if outlines is not None else {}),
                **({"crop_angles": angles} if angles is not None and any(angle is not None for angle in angles) else {}))
        return [provider.recognize(source,box,orientation,padding,
            **({"polygon": outlines[index]} if outlines is not None else {}),
            **({"crop_angle": angles[index]} if angles is not None and angles[index] is not None else {}))
            for index,(box,orientation) in enumerate(jobs)]

    cache = current_cloud_results.get()
    if cache is None:
        return infer(regions,polygons,crop_angles)
    config = getattr(provider,"config",{})
    name = getattr(getattr(provider,"capabilities",None),"name",type(provider).__qualname__)
    keys = [fingerprint({"format":2,"crop_version":OCR_CROP_VERSION,"kind":"native-ocr","provider":name,"config":config,
        "bbox":box,"polygon":polygons[index] if polygons is not None else None,
        "crop_angle":crop_angles[index] if crop_angles is not None else None,
        "orientation":orientation,"padding":padding}) for index,(box,orientation) in enumerate(regions)]
    values = [cache.get(key) for key in keys]
    missing = [index for index,value in enumerate(values) if value is None]
    if missing:
        jobs = [regions[index] for index in missing]
        recognized = infer(jobs,[polygons[index] for index in missing] if polygons is not None else None,
                           [crop_angles[index] for index in missing] if crop_angles is not None else None)
        if len(recognized)!=len(missing):
            raise ValueError("Native OCR result count mismatch")
        # Save before checking cancellation: the just-completed native call is
        # drained, but its valid result can still be reused by a future retry.
        for index,result in zip(missing,recognized,strict=True):
            values[index] = {"text":result.text,"confidence":result.confidence,
                             "orientation":result.orientation,"metadata":result.metadata}
            cache.put(keys[index],values[index])
    return [OCRResult(**value) for value in values]
