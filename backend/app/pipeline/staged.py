"""Run a stage against a private ORM adapter and immutable output directory."""
from __future__ import annotations

import asyncio
from copy import deepcopy
from pathlib import Path

from app.models import ModelConfig, ProcessingTask
from app.pipeline.processor import PAGE_DONE_STATUS, PipelineProcessor
from app.services.cloud_budget import checkpoint_requests
from app.services.quality import evaluate_page
from app.services.regions import synchronize_source_geometry_before_repair
from app.services.translation.context import page_context
from app.services.workflow_state import capture_page, decode, delta, private_page, requested_stages
from app.storage import StorageManager
from app.utils.text import has_text


class StageStorage(StorageManager):
    def __init__(self, root: Path, project_id: str, page_id: str, output: Path):
        super().__init__(root)
        self.project_id, self.page_id, self.output = project_id, page_id, self._inside(output)
        expected = self.root / "projects" / project_id / "pages" / page_id / "workflows"
        if not self.output.is_relative_to(expected.resolve()):
            raise ValueError("Stage output must belong to this page's workflow")

    def page_dir(self, project_id: str, page_id: str) -> Path:
        if (project_id, page_id) != (self.project_id, self.page_id):
            raise ValueError("A stage may only write its own page")
        for name in ("original", "masks", "bubbles", "clean", "rendered", "layers", "versions"):
            (self.output / name).mkdir(parents=True, exist_ok=True)
        return self.output

    def asset_page_dir(self, project_id: str, page_id: str) -> Path:
        return self._inside(self.root / "projects" / project_id / "pages" / page_id)


class StageProcessor(PipelineProcessor):
    def __init__(self, db, storage, policy, stage, check):
        super().__init__(db)
        self.storage = storage
        self.settings = self.settings.model_copy(update={**policy.get("settings", {}), "auto_provision_models": False})
        self._context_is_frozen = True
        self._previous_page_context = deepcopy(policy.get("previous_page"))
        self.stage, self.check = stage, check
        self.defer_text_rotation = (stage == "translation" and policy["request"].get("end_stage") == "rendering"
                                   and policy["request"].get("options", {}).get("repair_geometry", "source") == "source")

    def _check_interruption(self):
        self.check()

    def _initialize_text_rotation(self, region, evidence=None):
        # Angle/layout initialization belongs AFTER the repair-geometry join.
        # Rendering performs the same existing one-time initialization then.
        # Target-frame repair must first finalize that frame, so its serial
        # translation branch retains the original initialization order.
        return False if self.defer_text_rotation else super()._initialize_text_rotation(region, evidence)


def run_stage(stage: str, state: dict, policy: dict, output: Path, storage: StorageManager, check, progress=None) -> dict:
    """Only plain values in/out; the caller persists this delta as a checkpoint."""
    token = checkpoint_requests.set(True)
    try:
        return _run_stage(stage, state, policy, output, storage, check, progress)
    finally:
        checkpoint_requests.reset(token)


def _run_stage(stage, state, policy, output, storage, check, progress):
    check()
    with private_page(state) as (db, page):
        db.add_all(ModelConfig(**decode(ModelConfig, row)) for row in policy.get("models", []))
        db.commit()
        processor = StageProcessor(db, StageStorage(storage.root, page.project_id, page.id, output), policy, stage, check)
        processor._ocr_attempted_ids = set(state.get("ocr_attempted_ids", []))
        request = policy["request"]
        options = deepcopy(request.get("options", {}))
        force = bool(request.get("force", False))
        # An override selects the requested starting stage's model. Other
        # stages retain their own frozen defaults.
        provider = request.get("provider") if stage == request.get("start_stage") else None
        region_id = request.get("region_id")
        region_ids = set(options.get("region_ids", [])) or None
        if stage=="detection" or (stage=="ocr" and request.get("start_stage")!="translation"):
            metadata = dict(page.metadata_json or {})
            metadata.pop("ocr_exempt", None)
            page.metadata_json = metadata
        if stage == "detection":
            processor.detect(page, provider, force=force)
        elif stage == "ocr":
            # Translation prerequisites OCR only blanks in the eligible scope.
            if request.get("start_stage") == "translation":
                explicit_force = bool((region_id or region_ids) and force)
                region_ids = {region.id for region in processor._regions(page, region_id, region_ids)
                              if not has_text(region.source_text) and (not region.locked or explicit_force)
                              and (force or not has_text(region.translated_text))}
                region_id, provider = None, None
                if region_ids:
                    metadata = dict(page.metadata_json or {})
                    metadata.pop("ocr_exempt",None)
                    page.metadata_json = metadata
                    processor.ocr(page, provider, force=True, region_id=None, region_ids=region_ids,
                                  options=options, progress=progress)
            else:
                processor.ocr(page, provider, force=force, region_id=region_id, region_ids=region_ids,
                              options=options, progress=progress, overwrite_edits=force)
        elif stage == "translation":
            asyncio.run(processor.translate(page, provider, force=force, region_id=region_id,
                        region_ids=region_ids, options=options, progress=progress))
        elif stage == "mask":
            if options.get("repair_geometry", "source") == "translated" and "inpainting" in requested_stages(request):
                for region in processor._regions(page, region_id, region_ids):
                    synchronize_source_geometry_before_repair(db, region)
                db.commit()
            processor.generate_masks(page, force=force, region_id=region_id, region_ids=region_ids, options=options)
        elif stage == "inpainting":
            if options.get("repair_geometry", "source") == "translated" and request.get("start_stage") == "inpainting":
                for region in processor._regions(page, region_id, region_ids):
                    synchronize_source_geometry_before_repair(db, region)
                db.commit()
                processor.generate_masks(page,force=True,region_id=region_id,region_ids=region_ids,options=options)
                db.commit()
            processor.inpaint(page, provider, region_id=region_id, region_ids=region_ids,
                              rebuild_clean=bool(options.get("rebuild_clean", False)))
        elif stage == "rendering":
            processor.render(page, provider)
        else:
            raise ValueError("Unknown staged pipeline operation")
        if stage == request.get("end_stage"):
            page.status = PAGE_DONE_STATUS[stage]
            page.current_stage, page.error_message = None, None
            failed = [ProcessingTask(**decode(ProcessingTask, row)) for row in policy.get("failed_tasks", [])]
            issues = evaluate_page(page, failed)
            if stage == "rendering":
                page.status = "NEEDS_REVIEW" if issues else "COMPLETED"
        db.commit()
        check()
        after = capture_page(db, page.id, compact_history=False)
        result = {"changes": delta(state, after), "ocr_attempted_ids": sorted(processor._ocr_attempted_ids)}
        if stage == "translation":
            result["context"] = {"page_id": page.id, "regions": page_context(page.regions, character_budget=4000)}
        return result
