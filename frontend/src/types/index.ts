export type ViewMode = 'original' | 'clean' | 'translated' | 'comparison'
export type Tool = 'select' | 'rectangle' | 'polygon' | 'lasso' | 'mask-brush' | 'mask-eraser'

export interface Project {
  id: string
  name: string
  description: string
  source_language: string
  target_language: string
  translation_context: Record<string, unknown>
  settings: Record<string, unknown>
  cover_url: string | null
  page_count: number
  created_at: string
  updated_at: string
}

export interface ImagePage {
  id: string
  project_id: string
  filename: string
  width: number
  height: number
  order_index: number
  status: string
  current_stage: string | null
  error_message: string | null
  original_url: string
  clean_url: string | null
  rendered_url: string | null
  text_layer_url: string | null
  created_at: string
  updated_at: string
}

export interface TextRegion {
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
  font_family: string
  font_weight: number
  text_color: string
  stroke_color: string
  stroke_width: number
  alignment: 'left' | 'center' | 'right'
  line_spacing: number
  character_spacing: number
  rotation: number
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
}

export interface QualityIssue {
  project_id: string
  image_id: string
  region_id: string | null
  region_key: string | null
  code: string
  message: string
  severity: 'warning' | 'error'
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
  status: 'configured' | 'ready' | 'dependency_missing' | 'error'
  error: string | null
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
}
