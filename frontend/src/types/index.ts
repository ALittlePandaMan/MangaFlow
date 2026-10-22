export type ViewMode = 'original' | 'clean' | 'translated' | 'comparison' | 'mask'

export interface TextStyleDefaults {
  font_family: string
  font_size: number
  font_weight: number
  auto_font_size: boolean
  orientation: 'auto' | 'horizontal' | 'vertical'
  text_color: string
  stroke_color: string
  stroke_width: number
  alignment: 'left' | 'center' | 'right'
  line_spacing: number
  character_spacing: number
}

export interface EditorPreferences {
  shortcuts: Record<string, string>
  text_style?: TextStyleDefaults
}
export type Tool = 'pan' | 'select' | 'rectangle' | 'polygon' | 'lasso' | 'brush' | 'eraser'
export type RegionStageAction = 'ocr' | 'translate' | 'inpaint'

export interface EditorHistoryState {
  project_id: string
  image_id?: string
  undo_count: number
  redo_count: number
  undo_id: number | null
  redo_id: number | null
  undo_label: string | null
  redo_label: string | null
  pending?: boolean
}
export interface EditorHistoryResult {
  page: ImagePage | null
  pages: ImagePage[]
  regions: TextRegion[]
  history: EditorHistoryState
  region_ids: string[]
  action: string
}

export interface Project {
  id: string
  name: string
  description: string
  source_language: string
  target_language: string
  translation_context: Record<string, unknown>
  settings: Record<string, unknown>
  cover_url: string | null
  cover_thumbnail_url?: string | null
  page_count: number
  created_at: string
  updated_at: string
}

export interface ImagePage {
  text_classification?: {
    version: number
    status: string
    excluded_count: number
    policy?: {enabled: boolean; filter_sfx: boolean; min_sfx_confidence: number}
  } | null
  editor_history?: EditorHistoryState | null
  id: string
  project_id: string
  filename: string
  width: number
  height: number
  order_index: number
  status: string
  current_stage: string | null
  error_message: string | null
  ocr_exempt: boolean
  region_count?: number
  original_url: string
  thumbnail_url?: string | null
  thumbnail_2x_url?: string | null
  clean_url: string | null
  rendered_url: string | null
  text_layer_url: string | null
  text_layer_region_ids?: string[] | null
  created_at: string
  updated_at: string
}

export interface TextRegion {
  editor_history?: EditorHistoryState | null
  id: string
  image_id: string
  region_key: string
  polygon: number[][]
  bbox: number[]
  translated_polygon: number[][]
  translated_bbox: number[]
  mask_url: string | null
  source_text: string
  translated_text: string
  confidence: number
  orientation: 'horizontal' | 'vertical' | 'rotated'
  reading_order: number
  panel_id: string | null
  bubble_id: string | null
  region_type: 'bubble_simple' | 'bubble_complex' | 'background_simple' | 'background_complex' | 'sfx'
  font_size: number
  auto_font_size?: boolean
  font_family: string
  font_weight: number
  text_color: string
  stroke_color: string
  stroke_width: number
  alignment: 'left' | 'center' | 'right'
  line_spacing: number
  character_spacing: number
  rotation: number
  perspective_warp: boolean
  opacity: number
  locked: boolean
  visible: boolean
  needs_review: boolean
  review_reasons: string[]
  layout_warning: boolean
  layout_data: Record<string, unknown>
  updated_at: string
}

export interface ProcessingTask {
  id: string
  project_id: string | null
  image_id: string | null
  region_id: string | null
  task_type: string
  status: string
  progress: number
  current_stage: string | null
  message: string
  error_message: string | null
  payload: Record<string, unknown>
  pause_requested: boolean
  created_at: string
  updated_at: string
  batch_id?: string | null
  dependency_task_id?: string | null
  waiting_reason?: string | null
  error_kind?: string | null
  active_stages?: string[]
}

export interface ModelDescriptor {
  kind: string
  name: string
  description: string
  devices: string[]
  orientations: string[]
  supports_batch: boolean
  capabilities: Record<string, unknown>
  is_fallback: boolean
  installed: boolean
}

export interface ModelBootstrapEntry {
  id: string
  kind: string
  name: string
  provider: string
  action: 'created' | 'updated' | 'kept'
  installed: boolean
  status: 'configured' | 'ready' | 'configuration_required' | 'dependency_missing' | 'error'
  error: string | null
}

export interface DeviceProfile {
  platform: {
    system: string
    architecture: string
  }
  cpu: {
    logical_cores: number
    memory_gb: number | null
  }
  gpu: {
    available: boolean
    devices: Array<{
      index: number
      name: string
      memory_gb: number
      compute_capability: string
    }>
  }
  runtimes: {
    torch: {installed: boolean, cuda_available: boolean, version?: string, cuda_version?: string, error?: string}
    paddle: {installed: boolean, cuda_available: boolean, version?: string, error?: string}
  }
  recommendation: {
    profile: 'cpu' | 'gpu'
    provider_devices: Record<string, string>
    summary: string
  }
}

export interface SetupStatus {
  first_run: boolean
  ready: boolean
  validated: boolean
  stages: Array<{
    kind: string
    provider: string | null
    status: 'ready' | 'missing' | 'error'
    message: string
  }>
}

export interface ModelConfiguration {
  id: string
  kind: string
  name: string
  provider: string
  enabled: boolean
  is_default: boolean
  config: Record<string, unknown>
  has_api_key: boolean
  capabilities: Record<string, unknown>
}

export interface FontResource {
  name: string
  filename: string
  path: string
  url?: string
  preset_id?: string
  license_url?: string
  source_url?: string
}
export interface FontPreset {
  id: string
  name: string
  family: string
  description: string
  languages: string[]
  category: string
  size_bytes: number
  license: string
  source_url: string
  license_url: string
  installed_filename: string | null
}
export interface ExecutionPolicy {
  mode: 'auto' | 'fixed' | 'serial'
  max_pages: number
  cpu_workers: number
  memory_reserve_mb: number
  gpu_max_concurrency?: number
  gpu_memory_reserve_mb?: number
}

export interface ExecutionStatus {
  policy: ExecutionPolicy
  pages: {active:number,limit:number,maximum:number,peak:number,reason:string}
  cpu: {active:number,limit:number,maximum:number,peak:number,reason:string}
  models: {active:number,limit:number,maximum?:number,reason?:string,calls?:number,wait_seconds?:number,run_seconds?:number}
  inference?: Array<{kind:string; name:string; backend:string; device:string|null; state:string; fallback_reason:string|null}>
  gpu?: {known:boolean; devices:Array<{index:number; name:string; total_bytes:number; used_bytes:number; free_bytes:number; utilization_percent:number|null}>; reason?:string}
  stage_timings?: Array<{stage:string; samples:number; mean_seconds:number; p95_seconds:number}>
  resources: {known:boolean,available_bytes:number,total_bytes:number,cpu_percent:number,cpu_cores:number,rss_bytes:number}
  native_threads: number
  recent_stages: Array<{task_id:string,stage:string,seconds:number}>
}
