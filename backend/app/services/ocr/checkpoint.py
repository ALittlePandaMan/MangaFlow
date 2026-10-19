"""Native OCR partial results, scoped to the immutable page-stage lineage."""
from __future__ import annotations

from app.services.base import OCRResult
from app.services.cloud_results import current_cloud_results
from app.services.workflow_state import fingerprint


def recognize_local(provider,source,regions,padding,*,batch=False,check=None):
    cache = current_cloud_results.get()
    if cache is None:
        if batch:
            return provider.recognize_batch(source,regions,padding,check=check)
        return [provider.recognize(source,box,orientation,padding) for box,orientation in regions]
    config = getattr(provider,"config",{})
    name = getattr(getattr(provider,"capabilities",None),"name",type(provider).__qualname__)
    keys = [fingerprint({"format":1,"kind":"native-ocr","provider":name,"config":config,
        "bbox":box,"orientation":orientation,"padding":padding}) for box,orientation in regions]
    values = [cache.get(key) for key in keys]
    missing = [index for index,value in enumerate(values) if value is None]
    if missing:
        jobs = [regions[index] for index in missing]
        if batch:
            recognized = provider.recognize_batch(source,jobs,padding,check=check)
        else:
            recognized = [provider.recognize(source,box,orientation,padding) for box,orientation in jobs]
        if len(recognized)!=len(missing):
            raise ValueError("Native OCR result count mismatch")
        # Save before checking cancellation: the just-completed native call is
        # drained, but its valid result can still be reused by a future retry.
        for index,result in zip(missing,recognized,strict=True):
            values[index] = {"text":result.text,"confidence":result.confidence,
                             "orientation":result.orientation,"metadata":result.metadata}
            cache.put(keys[index],values[index])
    return [OCRResult(**value) for value in values]
