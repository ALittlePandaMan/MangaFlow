import {create} from 'zustand'
import type {CanvasTaskListProps} from '../components/CanvasTaskList'

export interface EditorActivitySource {
  owner: string
  target: HTMLDivElement
  props: CanvasTaskListProps
}

export const useActivityPanelSource = create<{source: EditorActivitySource | null}>(() => ({source: null}))
