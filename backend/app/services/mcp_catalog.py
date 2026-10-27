"""Small, explicit public API for comic translation. Web routes stay private."""
from copy import deepcopy

from mcp import types

STRING = {"type": "string", "minLength": 1}
NUMBER = {"type": "number"}
IDS = {"type": "array", "items": STRING, "maxItems": 2000, "uniqueItems": True}
BBOX = {"type": "array", "items": NUMBER, "minItems": 4, "maxItems": 4}
POINTS = {"type": "array", "items": {"type": "array", "items": NUMBER, "minItems": 2, "maxItems": 2}, "minItems": 3, "maxItems": 1000}
CHECK_RESULT = {"type": "object", "properties": {"status": {"enum": ["pass", "issue", "uncertain", "skipped"]},
    "reason": {"type": "string", "minLength": 1, "maxLength": 4000}}, "required": ["status", "reason"], "additionalProperties": False}
RUN_PAGE = {"run_id": STRING, "image_id": STRING}


def obj(properties, required=()):
    return {"type": "object", "properties": deepcopy(properties), "required": list(required), "additionalProperties": False}


def tool(name, description, properties=None, required=(), write=False, version=False):
    properties = deepcopy(properties or {})
    required = list(required)
    if write:
        properties["operation_id"] = {"type": "string", "minLength": 1, "maxLength": 80,
            "description": ("ID of the original accepted operation; inspection and acknowledgment target the SAME ID."
                if name == "operation_status" else "Unique string, e.g. book-page-edit-1; reuse only for identical retries. No UUID script needed.")}
        required.append("operation_id")
    if version:
        properties["expected_versions"] = {"type": "object", "additionalProperties": STRING, "minProperties": 1}
        required.append("expected_versions")
    return types.Tool(name=name, description=description, inputSchema=obj(properties, required),
        annotations=types.ToolAnnotations(readOnlyHint=not write, destructiveHint=name in {"page_edit", "mask_paint", "task_cancel"},
            idempotentHint=True, openWorldHint=name in {"workflow_advance", "task_retry"}))


# Restricted fields; never include locks, identities, arbitrary layout caches or settings.
EDIT_FIELDS = {
    "bbox": BBOX, "polygon": POINTS, "translated_bbox": BBOX, "translated_polygon": POINTS,
    "source_text": {"type": "string"}, "translated_text": {"type": "string"},
    "orientation": {"enum": ["vertical", "horizontal", "rotated"]}, "reading_order": {"type": "integer", "minimum": 0},
    "region_type": {"enum": ["bubble_simple", "bubble_complex", "sfx", "background_simple", "background_complex"]},
    "font_size": {"type": "number", "minimum": 1, "maximum": 512}, "auto_font_size": {"type": "boolean"},
    "font_family": STRING, "font_weight": {"type": "integer", "minimum": 100, "maximum": 900},
    "text_color": {"type": "string", "pattern": "^#[0-9a-fA-F]{6}$"},
    "stroke_color": {"type": "string", "pattern": "^#[0-9a-fA-F]{6}$"},
    "stroke_width": {"type": "number", "minimum": 0, "maximum": 50},
    "alignment": {"enum": ["left", "center", "right"]}, "line_spacing": {"type": "number", "minimum": .5, "maximum": 4},
    "character_spacing": {"type": "number", "minimum": -20, "maximum": 100},
    "rotation": {"type": "number", "minimum": -360, "maximum": 360}, "perspective_warp": {"type": "boolean"},
    "opacity": {"type": "number", "minimum": 0, "maximum": 1}, "visible": {"type": "boolean"},
}
CREATE_FIELDS = {**EDIT_FIELDS, "auto_orientation": {"type": "boolean",
    "description": "Infer writing direction during OCR; explicit orientation otherwise wins."}}
PAGINATION = {"offset": {"type": "integer", "minimum": 0}, "limit": {"type": "integer", "minimum": 1, "maximum": 50}}
IMPORT_PROPS = {"project_id": STRING, "project_name": {"type": "string", "minLength": 1, "maxLength": 200},
    "source_language": STRING, "target_language": STRING,
    "source_fingerprint": {"type": "string", "pattern": "^[a-f0-9]{64}$"},
    "asset_id": STRING, "images": {"type": "array", "minItems": 1, "maxItems": 2000,
        "items": obj({"asset_id": STRING, "relative_path": {"type": "string", "minLength": 1, "maxLength": 4096}}, ["asset_id", "relative_path"])}}
ISSUE = obj({"issue_id": STRING, "region_id": STRING,
    "category": {"enum": ["ocr", "translation", "geometry", "inpainting", "layout", "visual_review"]},
    "message": {"type": "string", "minLength": 1, "maxLength": 4000},
    "status": {"enum": ["open", "resolved", "uncertain", "unverified"]}}, ["category", "message", "status"])

CUSTOM = [
    tool("mcp_status", "Read connection capabilities and readiness. Local paths require the official stdio connector; missing configuration is fixed in the web settings. No keys returned."),
    tool("workflow_get", "Read the official recipe; role=worker returns focused page review/correction instructions for client-native subagents.", {"workflow_id": STRING, "role": {"enum": ["coordinator", "worker"]}}),
    tool("project_list", "Find existing projects and resumable runs; optional project_id returns ordered page summaries.",
        {"query": {"type": "string"}, "project_id": STRING, **PAGINATION}),
    tool("project_import", "Import uploaded images OR one comic archive/PDF asset; auto-create a named project or append to project_id. Backend validates/sorts atomically. Wait for job completion. Prefer local_import for local paths.", IMPORT_PROPS, write=True),
    tool("project_import_status", "Recover an import before retrying; checks receipt, file job and supplied assets without submitting work.",
        {"operation_id": {"type": "string", "minLength": 1, "maxLength": 80}, "asset_ids": IDS,
         "source_fingerprint": IMPORT_PROPS["source_fingerprint"]}, ["operation_id"]),
    tool("workflow_start", "Freeze ordered pages/configuration. Default single; parallel supports client-native workers sharing ONE run, and returns a coordinator_token for model tasks/export. Preserves manual work; agent translation mode remains coordinator-owned.",
        {"project_id": STRING, "workflow_id": STRING, "translation_mode": {"enum": ["configured", "agent"]},
         "coordination_mode": {"enum": ["single", "parallel"]},
         "image_ids": {**IDS, "minItems": 1}, "region_ids": {**IDS, "minItems": 1}}, ["project_id"], write=True),
    tool("workflow_status", "Read counts and ready calls with versions. Parallel worker_id/assignment_token filters owned pages; coordinator sees released batches and claim counts. No work is dispatched and no lease renewed. Default one actionable page; paginate details.",
        {"run_id": STRING, "include_details": {"type": "boolean"}, "worker_id": STRING, "assignment_token": STRING, **PAGINATION}, ["run_id"]),
    tool("workflow_advance", "Atomically queue up to 20 image_ids OR one image_id. New runs batch detection/OCR/translation first, with NO erasure until text review approves it. Preserves existing/manual work. Follow workflow_status to review other finished pages instead of serial task waits.",
        {"run_id": STRING, "action": {"enum": ["process", "claim", "renew", "release", "accept_configuration"]},
         "configuration_version": STRING,
         "image_id": STRING, "image_ids": {**IDS, "minItems": 1, "maxItems": 20}}, ["run_id"], write=True, version=True),
    tool("page_state", "Read page dimensions/version and paginated text, region keys and geometry. Region references accept this page's R001 keys or IDs. Refresh after edits/tasks; pagination needs expected_version. Details add styles/issues/hints.",
        {"image_id": STRING, "include_details": {"type": "boolean"}, "region_ids": {**IDS, "minItems": 1},
         "expected_version": STRING, "run_id": STRING, "details_offset": {"type": "integer", "minimum": 0}, **PAGINATION}, ["image_id"]),
    tool("page_review_plan", "Plan one numbered original for preflight, one original/translation comparison for final. Adds only missing readable tiles. VIEW returned page_preview images; re-plan with evidence IDs. Coverage is not a visual pass. Inspect clean/mask/detail crops when unclear.",
        {"image_id": STRING, "phase": {"enum": ["preflight", "final"]}, "expected_version": STRING,
         "evidence_ids": {**IDS, "maxItems": 256}, **PAGINATION}, ["image_id", "phase", "expected_version"]),
    tool("page_preview", "Return native PNG and evidence ID. comparison pairs original/translation with per-panel pixel mappings; annotated numbers source/translation frames. clean/mask and region_id/crop provide optional detail. Crops/edits use ORIGINAL pixels. Use current expected_version; no image scripts.",
        {"image_id": STRING, "kind": {"enum": ["original", "clean", "rendered", "mask", "annotated", "comparison"]},
         "region_id": STRING, "crop": BBOX, "expected_version": STRING,
         "frame": {"enum": ["source", "translation"]}, "max_size": {"type": "integer", "minimum": 256, "maximum": 2048}}, ["image_id"]),
    tool("workflow_review_page", "Record actual findings and issues while other pages run; use disposition=defer until the batch ends. Deferred preflight may omit selection and NEVER approves erasure. Passed text preflight needs translation plus source checks and explicit selection. Final issues use defer/fix/deliver_with_issues; no image capability => unverified.",
        {**RUN_PAGE, "phase": {"enum": ["preflight", "final"]}, "outcome": {"enum": ["passed", "issues", "unverified"]},
         "checks": {"type": "object", "additionalProperties": CHECK_RESULT}, "note": {"type": "string", "minLength": 1, "maxLength": 8000},
         "evidence_ids": {**IDS, "maxItems": 256},
         "selection": obj({"mode": {"enum": ["all_eligible", "regions"]}, "region_ids": IDS, "exclude": IDS}, ["mode"]),
         "preserved": {"type": "array", "maxItems": 2000, "items": obj({"region_id": STRING, "crop": BBOX, "reason": STRING}, ["reason"])},
         "issues": {"type": "array", "maxItems": 200, "items": ISSUE},
         "disposition": {"enum": ["fix", "defer", "deliver_with_issues"]}},
        ["run_id", "image_id", "phase", "outcome", "checks", "note"], write=True, version=True),
    tool("workflow_begin_correction", "Open a round for recorded issues after the initial text/output wave ends. Parallel runs require the page's current worker lease; later local processing blocks only its own page. TWO total rounds/page/run; edit locally, then review fresh evidence.",
        RUN_PAGE, ["run_id", "image_id"], write=True, version=True),
    tool("page_edit", "Atomically add/update/delete text regions on ONE page as one undoable operation. Use region keys or IDs. To split/merge delete old regions and add replacements together. Source-frame edits refresh local OCR; explicit source_text confirms it, empty source_text requests one OCR attempt. Frames use original pixels. Deletes restore artwork; wait for any rebuild_task.",
        {"image_id": STRING, "workflow_run_id": STRING,
         "add": {"type": "array", "maxItems": 200, "items": obj(CREATE_FIELDS)},
         "update": {"type": "array", "maxItems": 200, "items": obj({"region_id": STRING, "patch": obj(EDIT_FIELDS)}, ["region_id", "patch"])},
         "delete": {**IDS, "maxItems": 200}}, ["image_id"], write=True, version=True),
    tool("mask_paint", "Paint/erase a region's mask with an original-pixel polygon or stroke inside page bounds; preserves manual masks. Use image_id with R001 references. Returns changed and changed_bounds; unchanged strokes do not invalidate results. Then workflow_advance repairs affected regions or restores cleared artwork. This does not change OCR text.",
        {"region_id": STRING, "image_id": STRING, "workflow_run_id": STRING, "mode": {"enum": ["paint", "erase"]},
         "shape": {"enum": ["polygon", "stroke"]}, "points": {**POINTS, "minItems": 2},
         "width": {"type": "integer", "minimum": 1, "maximum": 100}}, ["region_id", "mode", "shape", "points"], write=True, version=True),
    tool("task_wait", "Read status or wait <=20 seconds for processing/file work. seconds=0 queries immediately. Disconnecting does not cancel. Returns compact progress, errors and protected export/report paths.",
        {"task_id": STRING, "kind": {"enum": ["processing", "file"]}, "seconds": {"type": "number", "minimum": 0, "maximum": 20}}, ["task_id", "kind"]),
    tool("task_cancel", "Explicitly cancel a processing task or file job; cancelled work never auto-resumes.",
        {"task_id": STRING, "kind": {"enum": ["processing", "file"]}}, ["task_id", "kind"], write=True),
    tool("task_retry", "Retry a clearly FAILED processing task, at most twice; rejects cancelled/paused/unknown paid outcomes. Failed imports/exports require inspecting the result before a new import/export operation.",
        {"task_id": STRING, "workflow_run_id": STRING}, ["task_id"], write=True, version=True),
    tool("operation_status", "Inspect accepted work; UNKNOWN reconciles an exact queued task without replay. If still unknown, inspect all run pages then accept_current_state with their expected_versions and note. Refuses active/ambiguous paid work; never replays the original request.",
        {"accept_current_state": {"type": "boolean"}, "expected_versions": {"type": "object", "additionalProperties": STRING, "minProperties": 1},
         "note": {"type": "string", "minLength": 1, "maxLength": 4000}}, write=True),
    tool("workflow_export", "Export every frozen reviewed page as CBZ (default) or translated PNG ZIP. Version-fenced snapshots reuse reviewed pixels. Separate JSON/Markdown reports disclose issues/unverified pages.",
        {"run_id": STRING, "kind": {"enum": ["cbz", "translated"]}}, ["run_id"], write=True),
]

# Keep a fixed tool list; coordination is optional on existing mutations.
for definition in CUSTOM:
    properties = definition.inputSchema["properties"]
    if definition.name in {"workflow_advance", "workflow_review_page", "workflow_begin_correction", "page_edit", "mask_paint"}:
        properties.update(worker_id={"type": "string", "minLength": 1, "maxLength": 80}, assignment_token=STRING)
    if definition.name in {"workflow_advance", "workflow_export", "task_retry", "task_cancel", "operation_status", "page_edit"}:
        properties["coordinator_token"] = STRING
    if definition.name == "workflow_advance":
        definition.description += " action=claim gets a page for worker_id; renew/release use its image_id and assignment_token. process (default) queues models. accept_configuration uses workflow_status's configuration_version and ALL page versions; archives old reviews, preserves content/rounds. Parallel process/accept require coordinator_token."
        definition.inputSchema["required"].remove("expected_versions")
        definition.inputSchema["allOf"] = [{"if": {"properties": {"action": {"enum": ["claim", "renew", "release"]}}, "required": ["action"]},
            "then": {"required": ["worker_id"]}, "else": {"required": ["expected_versions"]}},
            {"if": {"properties": {"action": {"enum": ["renew", "release"]}}, "required": ["action"]},
             "then": {"required": ["image_id", "assignment_token"]}},
            {"if": {"properties": {"action": {"const": "accept_configuration"}}, "required": ["action"]},
             "then": {"required": ["configuration_version"]}}]
