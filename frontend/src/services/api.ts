import {t} from '../i18n'
import type { DeviceProfile, FontPreset, FontResource, ImagePage, ModelBootstrapEntry, ModelConfiguration, ModelDescriptor, ProcessingTask, Project, RegionStageAction, SetupStatus, TextRegion } from '../types'
import type {EditorHistoryResult, EditorHistoryState} from '../types'
import type {ExecutionPolicy, ExecutionStatus} from '../types'
import type {EditorPreferences, TextStyleDefaults} from '../types'
import {uploadImagesInBatches, type ImageUploadProgress} from './imageUpload'

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message)
  }
}

export interface RecommendedSetupStatus {
  state: 'idle' | 'installing' | 'complete' | 'error'
  installed: boolean
  configured: boolean
  error: string | null
  models: Array<{kind:string, provider:string, name:string, installed:boolean, status:string, error:string|null}>
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
const responseFilename = (response: Response, fallback: string) => {
  const disposition = response.headers.get('Content-Disposition') || ''
  const encoded = disposition.match(/filename\*=UTF-8''([^;]+)/i)?.[1]
  if (encoded) {
    try { return decodeURIComponent(encoded) } catch { /* use the fallback */ }
  }
  return disposition.match(/filename="?([^";]+)"?/i)?.[1] || fallback
}

export const api = {
  execution: {
    get: () => request<ExecutionStatus>('/execution'),
    update: (policy: ExecutionPolicy) => request<ExecutionStatus>('/execution', {method:'PUT',body:json(policy)}),
  },
  history: {
    state: (id: string) => request<EditorHistoryState>(`/projects/${id}/history`),
    apply: (id: string, direction: 'undo' | 'redo', entryId: number, currentImageId?: string | null, operationId?:string) => request<EditorHistoryResult>(`/projects/${id}/history/${direction}`, {
      method:'POST', body:json({expected_entry_id:entryId,current_image_id:currentImageId,...(operationId ? {operation_id:operationId} : {})}),
    }),
  },
  projects: {
    reorderImages: (id: string, imageIds: string[]) => request<ImagePage[]>(`/projects/${id}/images/order`, {method:'POST',body:json(imageIds)}),
    list: () => request<Project[]>('/projects'),
    get: (id: string) => request<Project>(`/projects/${id}`),
    create: (payload: Partial<Project>) => request<Project>('/projects', { method: 'POST', body: json(payload) }),
    update: (id: string, payload: Partial<Project>) => request<Project>(`/projects/${id}`, { method: 'PATCH', body: json(payload) }),
    remove: (id: string) => request<void>(`/projects/${id}`, { method: 'DELETE' }),
    images: (id: string) => request<ImagePage[]>(`/projects/${id}/images`),
    upload: (id: string, files: File[], onProgress?: (progress: ImageUploadProgress) => void, signal?: AbortSignal) =>
      uploadImagesInBatches(files, batch => {
        const form = new FormData()
        batch.forEach(file => form.append('files', file))
        return request<ImagePage[]>(`/projects/${id}/images`, {method: 'POST', body: form, signal})
      }, onProgress, signal),
    import: async (file: File) => {
      const form = new FormData(); form.append('file', file)
      return request<Project>('/projects/import', {method: 'POST', body: form})
    },
    fonts: (id: string) => request<FontResource[]>(`/projects/${id}/fonts`),
    batch: (id: string, payload: typeof defaultProcess & {image_ids?: string[], only_unrecognized?: boolean, only_with_regions?: boolean} = defaultProcess) => request<ProcessingTask[]>(`/projects/${id}/batch-process`, { method: 'POST', body: json(payload) }),
    export: async (id: string, formats: string[], fallbackFilename = 'mangaflow-export.zip') => {
      const response = await fetch(`/api/projects/${id}/export`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: json({ formats }),
      })
      if (!response.ok) {
        let message = t("导出失败")
        try {message = (await response.json()).detail || message} catch { /* response is not JSON */ }
        throw new ApiError(response.status, message)
      }
      const blob = await response.blob()
      const url = URL.createObjectURL(blob)
      const link = document.createElement('a')
      link.href = url
      link.download = responseFilename(response, fallbackFilename)
      document.body.appendChild(link)
      link.click()
      link.remove()
      window.setTimeout(() => URL.revokeObjectURL(url), 1000)
    },
  },
  images: {
    get: (id: string) => request<ImagePage>(`/images/${id}`),
    regions: (id: string) => request<TextRegion[]>(`/images/${id}/regions`),
    remove: (id: string) => request<void>(`/images/${id}`, {method: 'DELETE'}),
    reset: (id: string) => request<ImagePage>(`/images/${id}/reset`, {method: 'POST'}),
    setOcrExempt: (id: string, exempt: boolean) => request<ImagePage>(`/images/${id}/ocr-exempt?exempt=${exempt}`, {method: 'PATCH'}),
    process: (id: string, payload = defaultProcess) => request<ProcessingTask>(`/images/${id}/process`, { method: 'POST', body: json(payload) }),
  },
  regions: {
    maskHistory:(id:string) => request<{region_updated_at:string,history:EditorHistoryState,
      undo:Array<{id:number,x:number,y:number,width:number,height:number,before_url:string,after_url:string}>,
      redo:Array<{id:number,x:number,y:number,width:number,height:number,before_url:string,after_url:string}>}>(`/regions/${id}/mask-history`),
    updateMany: (changes: Array<{id:string,patch:Partial<TextRegion>}>) => request<TextRegion[]>('/regions/bulk-update', {method:'POST',body:json(changes)}),
    create: (imageId: string, payload: Partial<TextRegion>) => request<TextRegion>(`/images/${imageId}/regions`, { method: 'POST', body: json(payload) }),
    update: (id: string, payload: Partial<TextRegion>) => request<TextRegion>(`/regions/${id}`, { method: 'PATCH', body: json(payload) }),
    remove: (id: string) => request<{region_id: string, region_keys?:Record<string,string>, rebuild_task: ProcessingTask | null, page?: ImagePage,editor_history?:EditorHistoryState}>(`/regions/${id}`, { method: 'DELETE' }),
    removeMany: (ids: string[]) => request<{region_ids: string[], region_keys?:Record<string,string>, rebuild_task: ProcessingTask | null, page?: ImagePage,editor_history?:EditorHistoryState}>('/regions/bulk-delete', { method: 'POST', body: json(ids) }),
    merge: (ids: string[]) => request<{region: TextRegion, region_keys?:Record<string,string>, rebuild_task: ProcessingTask | null}>('/regions/merge', { method: 'POST', body: json(ids) }),
    stage: (id: string, stage: RegionStageAction, options: Record<string, unknown> = {}) => request<ProcessingTask>(`/regions/${id}/${stage}`, {
      method: 'POST', body: json({ ...defaultProcess, provider: options.provider || null, options, force: true }),
    }),
    mask: async (id: string, blob: Blob, expectedUpdatedAt?: string, bounds?: {x:number,y:number}) => {
      const form = new FormData()
      form.append('file', blob, 'mask.png')
      if (expectedUpdatedAt) form.append('expected_updated_at', expectedUpdatedAt)
      if (bounds) {form.append('x',String(bounds.x));form.append('y',String(bounds.y))}
      return request<TextRegion>(`/regions/${id}/mask`, { method: 'PUT', body: form })
    },
  },
  tasks: {
    cancel: (id: string) => request<ProcessingTask>(`/tasks/${id}/cancel`, {method:'POST'}),
    statuses: (ids: string[]) => request<ProcessingTask[]>('/tasks/status', {method:'POST',body:json(ids)}),
    list: (projectId?: string, imageId?: string, page?: {limit: number, offset: number, activeFirst?: boolean}) => {
      const query = new URLSearchParams()
      if (projectId) query.set('project_id', projectId)
      if (imageId) query.set('image_id', imageId)
      if (page) {query.set('limit', String(page.limit)); query.set('offset', String(page.offset))}
      if (page?.activeFirst) query.set('active_first', 'true')
      const suffix = query.size ? `?${query.toString()}` : ''
      return request<ProcessingTask[]>(`/tasks${suffix}`)
    },
    get: (id: string) => request<ProcessingTask>(`/tasks/${id}`),
  },
  models: {
    retryClassification: (id: string) => request<{status:string}>(`/models/config/${id}/classification/retry`, {method:'POST'}),
    list: () => request<{available: ModelDescriptor[], configured: ModelConfiguration[]}>('/models'),
    deviceProfile: () => request<DeviceProfile>('/models/device-profile'),
    setupStatus: () => request<SetupStatus>('/models/setup-status'),
    recommendedStatus: () => request<RecommendedSetupStatus>('/models/recommended/status'),
    installRecommended: () => request<RecommendedSetupStatus>('/models/recommended/install', {method:'POST'}),
    verifySetup: () => request<SetupStatus>('/models/setup-status/verify', {method:'POST'}),
    discover: (payload: {base_url: string, api_protocol?: string, api_key?: string, config_id?: string}) => request<{models: string[], endpoint: string, base_url: string, protocol: string}>('/models/discover', {method: 'POST', body: json(payload)}),
    testConnection: (payload: Record<string, unknown>) => request<{ok:boolean, mode:'local'|'remote', message?:string, elapsed_ms:number}>('/models/test', {method:'POST', body:json(payload)}),
    create: (payload: Record<string, unknown>) => request<ModelConfiguration>('/models/config', { method: 'POST', body: json(payload) }),
    update: (id: string, payload: Record<string, unknown>) => request<ModelConfiguration>(`/models/config/${id}`, { method: 'PATCH', body: json(payload) }),
    bootstrap: (payload: {preload?: boolean, upgrade_fallbacks?: boolean, stages?: string[], selections?: Array<{kind: string, provider: string, device?: string, config?: Record<string, unknown>, api_key?: string}>} = {preload: true, upgrade_fallbacks: true}) => request<{ok: boolean, models: ModelBootstrapEntry[]}>('/models/bootstrap', { method: 'POST', body: json(payload) }),
  },
  fonts: {
    list: () => request<FontResource[]>('/fonts'),
    presets: () => request<FontPreset[]>('/fonts/presets'),
    install: (id: string) => request<FontResource>(`/fonts/presets/${encodeURIComponent(id)}/install`, {method:'POST'}),
    upload: async (file: File) => {
      const form = new FormData(); form.append('file', file)
      return request<FontResource>('/fonts', {method: 'POST', body: form})
    },
    remove: (filename: string) => request<void>(`/fonts/${encodeURIComponent(filename)}`, {method: 'DELETE'}),
  },
  preferences: {
    get: () => request<EditorPreferences>('/preferences'),
    update: (shortcuts: Record<string, string>) => request<EditorPreferences>('/preferences', {method:'PUT', body:json({shortcuts})}),
    updateTextStyle: (text_style: TextStyleDefaults) => request<EditorPreferences>('/preferences', {method:'PUT', body:json({text_style})}),
  },
}
