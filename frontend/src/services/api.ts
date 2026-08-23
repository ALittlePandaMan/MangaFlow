import type { FontResource, ImagePage, ModelBootstrapEntry, ModelConfiguration, ModelDescriptor, ProcessingTask, Project, QualityIssue, TextRegion } from '../types'

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message)
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const headers = new Headers(init?.headers)
  if (init?.body && !(init.body instanceof FormData)) headers.set('Content-Type', 'application/json')
  const response = await fetch(`/api${path}`, { ...init, headers })
  if (!response.ok) {
    let message = response.statusText
    try {
      const body = await response.json()
      message = typeof body.detail === 'string' ? body.detail : JSON.stringify(body.detail)
    } catch { /* response is not JSON */ }
    throw new ApiError(response.status, message)
  }
  if (response.status === 204) return undefined as T
  return response.json() as Promise<T>
}

const json = (value: unknown) => JSON.stringify(value)
const defaultProcess = { start_stage: 'detection', end_stage: 'ocr', force: false, options: {} }

export const api = {
  projects: {
    list: () => request<Project[]>('/projects'),
    get: (id: string) => request<Project>(`/projects/${id}`),
    create: (payload: Partial<Project>) => request<Project>('/projects', { method: 'POST', body: json(payload) }),
    update: (id: string, payload: Partial<Project>) => request<Project>(`/projects/${id}`, { method: 'PATCH', body: json(payload) }),
    uploadCover: (id: string, file: File) => {
      const form = new FormData(); form.append('file', file)
      return request<Project>(`/projects/${id}/cover`, {method:'PUT', body:form})
    },
    removeCover: (id: string) => request<Project>(`/projects/${id}/cover`, {method:'DELETE'}),
    remove: (id: string) => request<void>(`/projects/${id}`, { method: 'DELETE' }),
    images: (id: string) => request<ImagePage[]>(`/projects/${id}/images`),
    upload: async (id: string, files: File[]) => {
      const form = new FormData()
      files.forEach(file => form.append('files', file))
      return request<ImagePage[]>(`/projects/${id}/images`, { method: 'POST', body: form })
    },
    fonts: (id: string) => request<FontResource[]>(`/projects/${id}/fonts`),
    uploadFont: async (id: string, file: File) => {
      const form = new FormData(); form.append('file', file)
      return request<FontResource>(`/projects/${id}/fonts`, {method: 'POST', body: form})
    },
    batch: (id: string, payload = defaultProcess) => request<ProcessingTask[]>(`/projects/${id}/batch-process`, { method: 'POST', body: json(payload) }),
    review: (id: string) => request<QualityIssue[]>(`/projects/${id}/review`),
    exportUrl: (id: string) => `/api/projects/${id}/export`,
    export: async (id: string, formats: string[]) => {
      const response = await fetch(`/api/projects/${id}/export`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: json({ formats }),
      })
      if (!response.ok) throw new ApiError(response.status, '导出失败')
      const blob = await response.blob()
      const url = URL.createObjectURL(blob)
      const link = document.createElement('a')
      link.href = url
      link.download = 'mangaflow-export.zip'
      link.click()
      URL.revokeObjectURL(url)
    },
  },
  images: {
    get: (id: string) => request<ImagePage>(`/images/${id}`),
    regions: (id: string) => request<TextRegion[]>(`/images/${id}/regions`),
    remove: (id: string) => request<void>(`/images/${id}`, {method: 'DELETE'}),
    reorder: (id: string, orderIndex: number) => request<ImagePage>(`/images/${id}/order?order_index=${orderIndex}`, {method: 'PATCH'}),
    reset: (id: string) => request<ImagePage>(`/images/${id}/reset`, {method: 'POST'}),
    process: (id: string, payload = defaultProcess) => request<ProcessingTask>(`/images/${id}/process`, { method: 'POST', body: json(payload) }),
    stage: (id: string, stage: string, options: Record<string, unknown> = {}) => request<ProcessingTask>(`/images/${id}/${stage}`, {
      method: 'POST', body: json({ ...defaultProcess, options }),
    }),
  },
  regions: {
    create: (imageId: string, payload: Partial<TextRegion>) => request<TextRegion>(`/images/${imageId}/regions`, { method: 'POST', body: json(payload) }),
    update: (id: string, payload: Partial<TextRegion>) => request<TextRegion>(`/regions/${id}`, { method: 'PATCH', body: json(payload) }),
    remove: (id: string) => request<{region_id: string, rebuild_task: ProcessingTask | null}>(`/regions/${id}`, { method: 'DELETE' }),
    removeMany: (ids: string[]) => request<{region_ids: string[], rebuild_task: ProcessingTask | null}>('/regions/bulk-delete', { method: 'POST', body: json(ids) }),
    copy: (id: string) => request<TextRegion>(`/regions/${id}/copy`, { method: 'POST' }),
    split: (id: string, axis = 'auto') => request<TextRegion[]>(`/regions/${id}/split?axis=${axis}`, { method: 'POST' }),
    merge: (ids: string[]) => request<{region: TextRegion, rebuild_task: ProcessingTask | null}>('/regions/merge', { method: 'POST', body: json(ids) }),
    stage: (id: string, stage: string, options: Record<string, unknown> = {}) => request<ProcessingTask>(`/regions/${id}/${stage}`, {
      method: 'POST', body: json({ ...defaultProcess, provider: options.provider || null, options, force: true }),
    }),
    mask: async (id: string, blob: Blob) => {
      const form = new FormData()
      form.append('file', blob, 'mask.png')
      return request<TextRegion>(`/regions/${id}/mask`, { method: 'PUT', body: form })
    },
    maskOperation: (id: string, operation: string, amount: number) => request<TextRegion>(`/regions/${id}/mask/operation`, {
      method: 'POST', body: json({ operation, amount }),
    }),
  },
  tasks: {
    list: (projectId?: string) => request<ProcessingTask[]>(`/tasks${projectId ? `?project_id=${projectId}` : ''}`),
    get: (id: string) => request<ProcessingTask>(`/tasks/${id}`),
    action: (id: string, action: 'pause' | 'resume' | 'retry' | 'cancel') => request<ProcessingTask>(`/tasks/${id}/${action}`, { method: 'POST' }),
  },
  models: {
    list: () => request<{available: ModelDescriptor[], configured: ModelConfiguration[]}>('/models'),
    discover: (payload: {base_url: string, api_protocol?: string, api_key?: string, config_id?: string}) => request<{models: string[], endpoint: string, base_url: string, protocol: string}>('/models/discover', {method: 'POST', body: json(payload)}),
    create: (payload: Record<string, unknown>) => request<ModelConfiguration>('/models/config', { method: 'POST', body: json(payload) }),
    update: (id: string, payload: Record<string, unknown>) => request<ModelConfiguration>(`/models/config/${id}`, { method: 'PATCH', body: json(payload) }),
    bootstrap: (payload = {preload: true, upgrade_fallbacks: true}) => request<{ok: boolean, models: ModelBootstrapEntry[]}>('/models/bootstrap', { method: 'POST', body: json(payload) }),
    remove: (id: string) => request<void>(`/models/config/${id}`, { method: 'DELETE' }),
  },
  fonts: {
    list: () => request<FontResource[]>('/fonts'),
    upload: async (file: File) => {
      const form = new FormData(); form.append('file', file)
      return request<FontResource>('/fonts', {method: 'POST', body: form})
    },
    remove: (filename: string) => request<void>(`/fonts/${encodeURIComponent(filename)}`, {method: 'DELETE'}),
  },
}
