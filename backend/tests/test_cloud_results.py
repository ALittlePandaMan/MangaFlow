from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx
import pytest
from app.services import cloud_budget as budgets
from app.services.base import OCRResult, ProviderError
from app.services.cloud_results import CloudResultCache, CloudResultCorrupt, current_cloud_results
from app.services.ocr.concurrency import OCRJob, recognize_regions
from app.services.translation.providers import OpenAICompatibleTranslator
from app.services.workflow_state import fingerprint


@pytest.fixture(autouse=True)
def isolated_budgets():
    budgets._budgets.clear()
    yield
    budgets._budgets.clear()


def test_results_are_private_durable_checked_copies_and_can_only_be_extended(tmp_path):
    directory = tmp_path/"workflow"/".requests"
    cache = CloudResultCache(tmp_path,directory)
    key = fingerprint({"request":"identity","api_key":"not-written"})
    cache.put(key,{"one":"first"})
    cache.put(key,{"one":"first","two":"second"})
    reloaded = CloudResultCache(tmp_path,directory)
    value = reloaded.get(key)
    value["one"] = "changed"
    assert reloaded.get(key)=={"one":"first","two":"second"}
    assert "not-written" not in (directory/f"{key}.json").read_text()
    with pytest.raises(ValueError,match="rewritten"):
        cache.put(key,{"one":"replacement"})
    assert not list(directory.glob("*.tmp"))


def test_corruption_never_silently_pays_for_another_request_without_acknowledgement(tmp_path):
    key = fingerprint("request")
    cache = CloudResultCache(tmp_path,tmp_path/".requests")
    cache.put(key,{"one":"first"})
    path = cache.directory/f"{key}.json"
    path.write_text('{"version":1,"result":{"one":"tampered"}}')
    with pytest.raises(CloudResultCorrupt):
        cache.get(key)
    acknowledged = CloudResultCache(tmp_path,cache.directory,allow_invalid=True)
    assert acknowledged.get(key) is None
    assert len(list(cache.directory.glob("*.corrupt")))==1
    acknowledged.put(key,{"one":"new explicit result"})
    assert cache.get(key)=={"one":"new explicit result"}


def test_parallel_results_do_not_overwrite_each_other_or_escape_the_workflow(tmp_path):
    cache = CloudResultCache(tmp_path,tmp_path/".requests")
    with ThreadPoolExecutor(4) as workers:
        list(workers.map(lambda index:cache.put(fingerprint(index),{"text":str(index)}),range(24)))
    assert all(cache.get(fingerprint(index))=={"text":str(index)} for index in range(24))
    with pytest.raises(ValueError):
        cache.get("../../outside")
    with pytest.raises(ValueError):
        CloudResultCache(tmp_path,tmp_path.parent/"outside")


def test_request_duplicates_expire_only_after_the_whole_stage_is_saved(tmp_path):
    cache = CloudResultCache(tmp_path,tmp_path/".requests")
    key = fingerprint("request")
    cache.put(key,{"text":"saved"})
    forensic = cache.directory/"outcome.corrupt"
    forensic.write_text("preserved")
    cache.discard_saved()
    assert cache.get(key) is None and forensic.exists()


@pytest.mark.asyncio
async def test_partial_translation_is_saved_and_resume_requests_only_missing_ids(tmp_path,monkeypatch):
    calls = []

    def handler(request):
        payload = json.loads(request.content)
        ids = json.loads(payload["messages"][1]["content"].split("\n\n")[0])["required_output"]
        calls.append(set(ids))
        result = {key:"translated" for key in ids if key=="R001" or len(calls)>1}
        return httpx.Response(200,json={"choices":[{"message":{"content":json.dumps(result)}}]})

    client = httpx.AsyncClient
    monkeypatch.setattr(httpx,"AsyncClient",lambda **kwargs:client(transport=httpx.MockTransport(handler),**kwargs))
    provider = OpenAICompatibleTranslator({"base_url":"https://cached.invalid/v1","model":"test","api_key":"secret-not-written",
        "requests_per_minute":6000,"retries":2})
    cache_token = current_cloud_results.set(CloudResultCache(tmp_path,tmp_path/"stage"))
    checkpoint_token = budgets.checkpoint_requests.set(True)
    try:
        rows = [("R001","first"),("R002","second")]
        with pytest.raises(ProviderError,match="missing"):
            await provider.translate_regions(rows,source_language="ja",target_language="zh",context={})
        assert len(calls)==1  # Unknown/partial outcomes are not auto-retried.
        result = await provider.translate_regions(rows,source_language="ja",target_language="zh",context={})
        assert result=={"R001":"translated","R002":"translated"}
        assert calls==[{"R001","R002"},{"R002"}]
        assert await provider.translate_regions(rows,source_language="ja",target_language="zh",context={})==result
        assert len(calls)==2
        assert all("secret-not-written" not in path.read_text() for path in (tmp_path/"stage").glob("*.json"))
    finally:
        current_cloud_results.reset(cache_token)
        budgets.checkpoint_requests.reset(checkpoint_token)


def test_partial_ocr_reuses_completed_regions_without_spending_request_permits(tmp_path):
    calls = []

    class OCR:
        config = {"base_url":"https://ocr-cache.invalid","model":"test","max_concurrency":1,
            "concurrency_mode":"fixed","requests_per_minute":6000,"retries":0}

        def recognize_once(self,source,box,orientation,padding):
            calls.append(box[0])
            if calls==[0,20]:
                raise ProviderError("offline",retryable=True,status_code=503,retry_after=0)
            return OCRResult("recognized",.9,orientation)

    provider = OCR()
    jobs = [OCRJob(str(index),(left,0,10,10),"vertical") for index,left in enumerate((0,20))]
    cache_token = current_cloud_results.set(CloudResultCache(tmp_path,tmp_path/"stage"))
    checkpoint_token = budgets.checkpoint_requests.set(True)
    try:
        with pytest.raises(ProviderError,match="offline"):
            list(recognize_regions(provider,Path("not-read"),jobs,4,lambda:None,lambda _:None))
        budget = budgets.request_budget(provider.config)
        budget.cooldown_until = budget.next_start = 0
        first = next(recognize_regions(provider,Path("not-read"),jobs[:1],4,lambda:None,lambda _:None))
        assert first[0]=="0" and budget.active==0
        results = list(recognize_regions(provider,Path("not-read"),jobs,4,lambda:None,lambda _:None))
        assert len(results)==2 and calls==[0,20,20]
    finally:
        current_cloud_results.reset(cache_token)
        budgets.checkpoint_requests.reset(checkpoint_token)
