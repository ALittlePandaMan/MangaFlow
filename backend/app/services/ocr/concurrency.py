"""Bounded OCR scheduler. Workers get immutable crops/IDs, never ORM sessions.

Only this scheduler retries batch OCR. Completed regions are never resubmitted.
Closing the iterator stops dispatch and drains in-flight HTTP calls (which may
already have been billed), so a cancelled task cannot leave orphan requests.
"""
from __future__ import annotations

import time
from collections.abc import Callable, Iterator
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from contextvars import copy_context
from dataclasses import dataclass
from pathlib import Path

from app.services.base import OCRResult, ProviderError
from app.services.cloud_budget import can_retry_request, request_budget
from app.services.cloud_results import current_cloud_results
from app.services.ocr.crops import OCR_CROP_VERSION
from app.services.workflow_state import fingerprint


@dataclass(frozen=True)
class OCRJob:
    region_id: str
    bbox: tuple[float, ...]
    orientation: str
    polygon: tuple[tuple[float, float], ...] | None = None
    crop_angle: float | None = None


def recognize_regions(provider, source: Path, jobs: list[OCRJob], padding: int, check: Callable[[], None], report: Callable[[str], None]) -> Iterator[tuple[str, OCRResult]]:
    budget = request_budget(provider.config)
    budget.configure(provider.config)
    maximum = int(provider.config.get("max_concurrency", 8))
    retries = int(provider.config.get("retries", 2))
    queued = [(job, 0, 0.0) for job in jobs]
    futures: dict[Future, tuple[OCRJob, int]] = {}
    last_report = 0.0
    cache = current_cloud_results.get()
    keys = {job.region_id:fingerprint({"format":2,"crop_version":OCR_CROP_VERSION,"kind":"ocr","config":provider.config,
        "region_id":job.region_id,"bbox":job.bbox,"polygon":job.polygon,"crop_angle":job.crop_angle,
        "orientation":job.orientation,"padding":padding}) for job in jobs} if cache else {}

    def perform(job: OCRJob) -> OCRResult:
        started = time.monotonic()
        error = None
        try:
            geometry = {"polygon": [list(point) for point in job.polygon]} if job.polygon is not None else {}
            if job.crop_angle is not None:
                geometry["crop_angle"] = job.crop_angle
            result = provider.recognize_once(source, list(job.bbox), job.orientation, padding, **geometry)
            if cache:
                cache.put(keys[job.region_id],{"text":result.text,"confidence":result.confidence,
                    "orientation":result.orientation,"metadata":result.metadata})
            return result
        except ProviderError as exc:
            error = exc
            raise
        except Exception:
            error = ProviderError("云端 OCR 处理失败，原文未更新。")
            raise error from None
        finally:
            budget.finish(time.monotonic() - started, error)

    with ThreadPoolExecutor(max_workers=maximum, thread_name_prefix="cloud-ocr") as executor:
        try:
            while queued or futures:
                check()
                now = time.monotonic()
                # Handle errors before dispatching replacement jobs.
                for future in [item for item in futures if item.done()]:
                    job, attempt = futures.pop(future)
                    try:
                        result = future.result()
                    except ProviderError as error:
                        if not can_retry_request(error) or attempt >= retries:
                            raise
                        queued.append((job, attempt + 1, now + min(2 ** attempt, 8)))
                    else:
                        check()
                        yield job.region_id, result
                while queued and len(futures) < maximum:
                    ready = next((i for i, (_, _, at) in enumerate(queued) if at <= time.monotonic()), None)
                    if ready is None:
                        break
                    job = queued[ready][0]
                    saved = cache.get(keys[job.region_id]) if cache else None
                    if saved is not None:
                        queued.pop(ready)
                        check()
                        yield job.region_id, OCRResult(**saved)
                        continue
                    if not budget.try_acquire():
                        break
                    job, attempt, _ = queued.pop(ready)
                    try:
                        # Carry timing and checkpoint/retry policy into the
                        # worker without sharing a database session or ORM row.
                        futures[executor.submit(copy_context().run, perform, job)] = (job, attempt)
                    except BaseException:
                        budget.finish(0, ProviderError("任务已停止"), learn=False)
                        raise
                if now - last_report >= .5:
                    report(budget.status())
                    last_report = now
                if futures:
                    wait(futures, timeout=budget.poll_interval(), return_when=FIRST_COMPLETED)
                elif queued:
                    time.sleep(.1)
        finally:
            queued.clear()
            # No queued executor work: submissions never exceed worker count.
            # The context manager waits for bounded in-flight calls, without DB writes.
