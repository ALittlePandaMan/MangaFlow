import { create } from 'zustand'
import type { Tool, ViewMode } from '../../types'

export interface LayerVisibility {
  original: boolean
  detection: boolean
  clean: boolean
  translated: boolean
  mask: boolean
}

interface EditorState {
  view: ViewMode
  tool: Tool
  maskOperation: 'brush' | 'eraser'
  zoom: number
  viewReset: number
  selectedIds: string[]
  layers: LayerVisibility
  setView: (view: ViewMode) => void
  setTool: (tool: Tool) => void
  setMaskOperation: (operation: 'brush' | 'eraser') => void
  setZoom: (zoom: number) => void
  resetView: () => void
  select: (id: string | null, additive?: boolean) => void
  selectMany: (ids: string[]) => void
  toggleLayer: (name: keyof LayerVisibility) => void
}

const viewLayers: Record<ViewMode, LayerVisibility> = {
  original: { original: true, detection: true, clean: false, translated: false, mask: false },
  clean: { original: false, detection: true, clean: true, translated: false, mask: false },
  translated: { original: false, detection: true, clean: true, translated: true, mask: false },
  comparison: { original: false, detection: false, clean: false, translated: false, mask: false },
  mask: { original: false, detection: false, clean: false, translated: false, mask: false },
}

export const useEditorStore = create<EditorState>((set) => ({
  view: 'translated',
  tool: 'select',
  maskOperation: 'brush',
  zoom: 1,
  viewReset: 0,
  selectedIds: [],
  layers: viewLayers.translated,
  setView: view => set(state => ({ view, layers: { ...viewLayers[view] }, tool: view !== 'mask' && (state.tool === 'brush' || state.tool === 'eraser') ? 'select' : state.tool })),
  setTool: tool => set(state => {
    const createsRegion = state.view !== 'mask' && (tool === 'rectangle' || tool === 'polygon' || tool === 'lasso')
    return {
      tool,
      maskOperation: tool === 'brush' || tool === 'eraser' ? tool : state.maskOperation,
      // A polygon draft is drawn independently of the detection layer, while
      // completed regions belong to that layer. Keep it visible so closing a
      // draft cannot make a newly-created region appear to vanish.
      layers: createsRegion && !state.layers.detection
        ? {...state.layers, detection: true}
        : state.layers,
    }
  }),
  setMaskOperation: operation => set(state => ({
    maskOperation: operation,
    // Add/erase and the selected shape are independent. Navigation tools
    // return to freehand; a selected rectangle/polygon/lasso stays selected.
    tool: state.tool === 'rectangle' || state.tool === 'polygon' || state.tool === 'lasso' ? state.tool : operation,
  })),
  setZoom: zoom => set({ zoom: Math.min(6, Math.max(0.05, zoom)) }),
  resetView: () => set(state => ({zoom: 1, viewReset: state.viewReset + 1})),
  select: (id, additive = false) => set(state => {
    if (!id) return { selectedIds: [] }
    if (additive) return { selectedIds: state.selectedIds.includes(id) ? state.selectedIds.filter(item => item !== id) : [...state.selectedIds, id] }
    return { selectedIds: [id] }
  }),
  selectMany: ids => set({ selectedIds: [...new Set(ids)] }),
  toggleLayer: name => set(state => ({ layers: { ...state.layers, [name]: !state.layers[name] } })),
}))
