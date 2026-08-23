import { create } from 'zustand'
import type { Tool, ViewMode } from '../types'

export interface LayerVisibility {
  original: boolean
  detection: boolean
  masks: boolean
  clean: boolean
  translated: boolean
}

interface EditorState {
  view: ViewMode
  tool: Tool
  zoom: number
  selectedIds: string[]
  layers: LayerVisibility
  brushSize: number
  brushHardness: number
  setView: (view: ViewMode) => void
  setTool: (tool: Tool) => void
  setZoom: (zoom: number) => void
  select: (id: string | null, additive?: boolean) => void
  selectMany: (ids: string[]) => void
  toggleLayer: (name: keyof LayerVisibility) => void
  setBrush: (size: number, hardness: number) => void
}

const viewLayers: Record<ViewMode, LayerVisibility> = {
  original: { original: true, detection: true, masks: false, clean: false, translated: false },
  clean: { original: false, detection: true, masks: false, clean: true, translated: false },
  translated: { original: false, detection: true, masks: false, clean: true, translated: true },
  comparison: { original: false, detection: false, masks: false, clean: false, translated: false },
}

export const useEditorStore = create<EditorState>((set) => ({
  view: 'translated',
  tool: 'select',
  zoom: 1,
  selectedIds: [],
  layers: viewLayers.translated,
  brushSize: 28,
  brushHardness: 0.8,
  setView: view => set({ view, layers: { ...viewLayers[view] } }),
  setTool: tool => set({ tool }),
  setZoom: zoom => set({ zoom: Math.min(6, Math.max(0.05, zoom)) }),
  select: (id, additive = false) => set(state => {
    if (!id) return { selectedIds: [] }
    if (additive) return { selectedIds: state.selectedIds.includes(id) ? state.selectedIds.filter(item => item !== id) : [...state.selectedIds, id] }
    return { selectedIds: [id] }
  }),
  selectMany: ids => set({ selectedIds: [...new Set(ids)] }),
  toggleLayer: name => set(state => ({ layers: { ...state.layers, [name]: !state.layers[name] } })),
  setBrush: (brushSize, brushHardness) => set({ brushSize, brushHardness }),
}))
