import {t} from '../i18n'
import type {ImagePage} from '../types'

const MiB = 1024 * 1024
const MIN_BATCH_BYTES = MiB
const MAX_BATCH_BYTES = 48 * MiB
const MAX_BATCH_FILES = 32

export type ImageUploadProgress = {
  completedFiles: number
  totalFiles: number
  completedBytes: number
  totalBytes: number
  batchFiles: number
  batchBytes: number
  resizing: boolean
}

export class ImageUploadError extends Error {
  constructor(
    public completed: ImagePage[],
    public totalFiles: number,
    public uncertain: boolean,
    message: string,
    cause: unknown,
  ) {
    super(message, {cause})
  }
}

/** Keep only the current multipart request in flight; never decode/read all files into memory. */
export async function uploadImagesInBatches(
  files: File[],
  send: (batch: File[]) => Promise<ImagePage[]>,
  onProgress?: (progress: ImageUploadProgress) => void,
  signal?: AbortSignal,
): Promise<ImagePage[]> {
  const completed: ImagePage[] = []
  const totalBytes = files.reduce((total, file) => total + file.size, 0)
  let completedBytes = 0
  let cursor = 0
  let budget = 16 * MiB
  let ceiling = MAX_BATCH_BYTES
  let fileLimit = MAX_BATCH_FILES
  let resizing = false

  while (cursor < files.length) {
    signal?.throwIfAborted()
    // Reserve room for multipart boundaries and UTF-8 filenames, not just image bytes.
    let requestBytes = 4096
    let batchBytes = 0
    let end = cursor
    while (end < files.length && end - cursor < fileLimit) {
      const file = files[end]
      const estimatedBytes = file.size + 1024 + file.name.length * 4
      if (end > cursor && requestBytes + estimatedBytes > budget) break
      requestBytes += estimatedBytes
      batchBytes += file.size
      end++
    }
    const batch = files.slice(cursor, end)
    onProgress?.({completedFiles: cursor, totalFiles: files.length, completedBytes, totalBytes,
      batchFiles: batch.length, batchBytes, resizing})
    const started = performance.now()
    let added: ImagePage[]
    try {
      added = await send(batch)
      if (!Array.isArray(added) || added.length !== batch.length) {
        throw new Error(t("服务器返回的图片数量与本批次不一致"))
      }
    } catch (cause) {
      const status = cause && typeof cause === 'object' && 'status' in cause ? Number(cause.status) : 0
      // 413 rejects the whole request before import. Never blindly retry network/5xx
      // failures: the server may have already committed the batch, causing duplicates.
      if (status === 413 && batch.length > 1) {
        ceiling = Math.min(ceiling, 4096 + Math.floor((requestBytes - 4096) / 2))
        budget = Math.min(budget, ceiling)
        fileLimit = Math.max(1, Math.floor(batch.length / 2))
        resizing = true
        continue
      }
      const uncertain = ![400, 401, 403, 404, 409, 413, 415, 422].includes(status)
      const reason = status === 413
        ? t("单张图片「{{v0}}」超过服务器请求大小限制，分批无法拆分单张图片。", {v0: batch[0].name})
        : `${cause instanceof Error ? cause.message : String(cause)}。`
      const next = uncertain
        ? t("本批次结果未能确认，请先检查页面列表，避免重复导入。")
        : t("尚有 {{v0}} 张未导入（从「{{v1}}」开始），可以只重选剩余图片继续导入。", {v0: files.length - cursor, v1: batch[0].name})
      throw new ImageUploadError([...completed], files.length, uncertain,
        t("已成功导入 {{v0}}/{{v1}} 张。{{v2}}{{v3}}", {v0: cursor, v1: files.length, v2: reason, v3: next}), cause)
    }
    completed.push(...added)
    completedBytes += batchBytes
    cursor = end
    resizing = false
    // Target short requests. Slow disks/connections shrink subsequent batches;
    // fast requests grow gradually without exceeding learned proxy limits.
    const elapsed = performance.now() - started
    const factor = elapsed > 5000 ? .5 : elapsed < 1500 ? 1.5 : 1
    budget = Math.min(ceiling, Math.max(MIN_BATCH_BYTES, Math.floor(budget * factor)))
    fileLimit = Math.min(MAX_BATCH_FILES, Math.max(1, factor > 1 ? Math.ceil(fileLimit * factor) : Math.floor(fileLimit * factor)))
  }
  if (files.length) onProgress?.({completedFiles: cursor, totalFiles: files.length, completedBytes,
    totalBytes, batchFiles: 0, batchBytes: 0, resizing: false})
  return completed
}
