from __future__ import annotations

from app.services.base import OCRResult
from app.services.cloud_results import CloudResultCache, current_cloud_results
from app.services.ocr.checkpoint import recognize_local


def test_native_partial_results_survive_interruption_and_order_is_preserved(tmp_path):
    calls = []

    class OCR:
        config = {}

        def recognize(self,source,box,orientation,padding):
            calls.append(box[0])
            return OCRResult(str(box[0]),.9,orientation)

        def recognize_batch(self,source,jobs,padding,check=None):
            return [self.recognize(source,box,orientation,padding) for box,orientation in jobs]

    token = current_cloud_results.set(CloudResultCache(tmp_path,tmp_path/"native"))
    provider = OCR()
    first = ([0,0,10,10],"vertical")
    second = ([20,0,10,10],"horizontal")
    try:
        assert recognize_local(provider,None,[first],4)[0].text=="0"
        assert [row.text for row in recognize_local(provider,None,[second,first],4,batch=True)]==["20","0"]
        assert calls==[0,20]
        # Crop/config changes do not accidentally reuse another inference.
        recognize_local(provider,None,[first],8)
        assert calls==[0,20,0]
    finally:
        current_cloud_results.reset(token)
