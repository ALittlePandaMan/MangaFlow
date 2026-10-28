# MangaFlow local MCP connector 2.1

Portable Node.js stdio bridge for MangaFlow's 20 comic translation tools. Adds only
`local_import`, `local_download`, `local_transfer_status`, and `local_transfer_cancel`.
Models run on the MangaFlow backend.

New workflows batch detection/OCR/translation first, review completed pages while
other tasks run, then correct and batch erasure/typesetting. Follow `workflow_status`
for ready actions; the connector forwards the backend's current schemas and prompts.

Workflow 2.2 supports client-native parallel workers when both MCP and image viewing
are available in children. The client chooses concurrency; MangaFlow does not launch
reasoning agents. The coordinator starts one run with `coordination_mode=parallel`,
keeps its `coordinator_token`, and delegates `workflow_get(role=worker)`, run ID and
distinct worker IDs. Workers use `workflow_advance(action=claim|renew|release)`;
page leases last 15 minutes and protect edits/reviews against stale workers.
The coordinator alone queues models, controls tasks and exports. No new connector
tools are required; existing connectors forward the backend's current tool schemas.

## Install or upgrade

Give the Agent the installation request from Settings → MCP. It uses the current
website's protocol, host and port unless an explicit remote URL is configured.
For example, a deployment at `http://localhost:18080` serves the installation guide
at `http://localhost:18080/mangaflow/setup`.
The official standalone installer downloads a checksum-verified package, runs
`npm ci --ignore-scripts` in `~/.mangaflow/mcp-connector`, and probes real stdio.
It returns absolute Node/index paths for the current client's MCP configuration.
Preserve other servers, reload/reconnect, and confirm `mcp_status` reports
`transport=stdio` and `local_import=true`. Re-run the installer to upgrade older connectors.

Manual setup: download the connector ZIP from Settings, extract it, run `npm ci`
inside it, and configure `node <absolute-index.mjs>`. Requires Node.js >=20.11.
Set `MANGAFLOW_MCP_URL` to the address shown in Settings → MCP. There is no fixed-port
fallback: the connector fails clearly when this variable is missing. The installer
accepts `--url` or `MANGAFLOW_MCP_URL`, with the command-line value taking precedence.
After changing the deployment port, copy an updated configuration or rerun the installer
and reconnect the client. Existing saved client configurations are not rewritten by the server. Remote HTTPS
and legacy `/mcp` use `MANGAFLOW_MCP_TOKEN`. Keep the token out of prompts/config examples.

## Local files

```json
{
  "path": "D:\\漫画\\Example",
  "target_language": "en",
  "operation_id": "example-import-1"
}
```

Pass this to `local_import` for a directory, image, ZIP/CBZ/RAR/CBR or PDF.
Directories recursively collect images, ignore links/metadata/nested archives,
and naturally sort complete relative paths. The backend validates everything and
publishes one atomic import. A new project uses the directory/file name; optional
`project_id` appends without changing an existing project's name/languages.

Poll `local_transfer_status` when RUNNING. COMPLETED means the file job was accepted;
wait for `result.job.id` using `task_wait(kind=file)` before starting translation.
Use `project_list` to check imported order. No manual project creation, extraction,
image-processing scripts or additional libraries are necessary.

Import checkpoints under `~/.mangaflow/mcp-transfers` survive restarts (override
`MANGAFLOW_MCP_STATE_DIR`). They contain paths, metadata and asset/job references,
never tokens or complete files. Resume INTERRUPTED work using the SAME operation ID
and arguments. Changed arguments conflict; unknown submissions require inspection.
Checkpoints from prior directory imports remain usable after upgrading.

`local_transfer_cancel` stops upload/download work and cancels accepted import jobs
when possible. Cancellation never resumes automatically. `local_download` streams
returned export/preview/report paths to an explicit destination and never overwrites
an existing file. Download transfer status is limited to the current connector session.

## Translation and review

Read `workflow_get` once, then follow compact `workflow_status.next_steps` and
`workflow_advance`. Use `page_edit` for text/geometry and `mask_paint` for local repair.
Refresh versions/images after changes. Image evidence does not prove a model viewed
or understood it; clients unable to view images must record unverified.

MCP work is queried through MCP; the web activity list shows web submissions.
The backend still shares project data, locks, queue and models across both interfaces.
Management tools and arbitrary backend requests are unavailable through MCP 2.0.
