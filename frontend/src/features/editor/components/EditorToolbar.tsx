import { BoxSelect, Focus, LassoSelect, MousePointer2, PenTool, Redo2, Undo2, ZoomIn, ZoomOut } from 'lucide-react'
import type { Tool, ViewMode } from '../../../types'
import {cn} from '../../../ui'
import {formatShortcut, shortcutToAria, type ShortcutId, useShortcutStore} from '../../shortcuts/store'
import { useEditorStore } from '../store'

const tools: {id: Tool, title: string, shortcutId: ShortcutId, icon: React.ReactNode}[] = [
  {id: 'select', title: '选择 / 变换', shortcutId: 'tool.select', icon: <MousePointer2 size={17}/>},
  {id: 'rectangle', title: '矩形区域', shortcutId: 'tool.rectangle', icon: <BoxSelect size={17}/>},
  {id: 'polygon', title: '多边形区域（点击首点闭合）', shortcutId: 'tool.polygon', icon: <PenTool size={17}/>},
  {id: 'lasso', title: '套索区域', shortcutId: 'tool.lasso', icon: <LassoSelect size={17}/>},
]

const views: {id: ViewMode, label: string, shortcutId: ShortcutId, title?: string}[] = [
  {id: 'original', label: '原图', shortcutId: 'view.original'},
  {id: 'clean', label: '净图', shortcutId: 'view.clean'},
  {id: 'translated', label: '译文', shortcutId: 'view.translated'},
  {id: 'comparison', label: '对比', shortcutId: 'view.comparison', title: '原图 / 译文左右对比'},
]

export function EditorToolbar({ onUndo, onRedo, canUndo, canRedo, disabled = false, centerContent, rightActions }: {onUndo: () => void, onRedo: () => void, canUndo: boolean, canRedo: boolean, disabled?: boolean, centerContent?: React.ReactNode, rightActions?: React.ReactNode}) {
  const tool = useEditorStore(state => state.tool)
  const setTool = useEditorStore(state => state.setTool)
  const view = useEditorStore(state => state.view)
  const setView = useEditorStore(state => state.setView)
  const shortcuts = useShortcutStore(state => state.shortcuts)
  const toolButton = 'grid size-8 min-h-8 cursor-pointer place-items-center rounded-lg border border-transparent bg-transparent p-0 text-muted outline-none transition-colors hover:bg-hover hover:text-ink disabled:cursor-not-allowed disabled:opacity-30 focus-visible:ring-2 focus-visible:ring-accent/30'
  return <div className="flex h-12 items-center border-b border-line bg-surface px-4" role="toolbar" aria-label="编辑器顶部工具栏">
    <div className="flex gap-1.5">{tools.map(item => <button disabled={disabled} key={item.id} className={cn(toolButton, tool === item.id && '!border-accent !bg-accent !text-accent-ink shadow-[0_0_0_2px_rgb(16_211_163/.12)] hover:!bg-accent-hover hover:!text-accent-ink')} title={`${item.title} (${formatShortcut(shortcuts[item.shortcutId])})`} aria-label={item.title} aria-keyshortcuts={shortcutToAria(shortcuts[item.shortcutId])} aria-pressed={tool === item.id} onClick={() => setTool(item.id)}>{item.icon}</button>)}</div>
    <span className="mx-3 h-5 border-l border-line" />
    <div className="flex gap-1.5"><button className={toolButton} disabled={disabled || !canUndo} onClick={onUndo} title={`撤销 (${formatShortcut(shortcuts['edit.undo'])})`} aria-label="撤销" aria-keyshortcuts={shortcutToAria(shortcuts['edit.undo'])}><Undo2 size={17}/></button><button className={toolButton} disabled={disabled || !canRedo} onClick={onRedo} title={`重做 (${formatShortcut(shortcuts['edit.redo'])})`} aria-label="重做" aria-keyshortcuts={shortcutToAria(shortcuts['edit.redo'])}><Redo2 size={17}/></button></div>
    <span className="mx-3 h-5 border-l border-line" />
    <div className="flex overflow-hidden rounded-lg border border-line bg-panel">{views.map(item => <button disabled={disabled} className={cn('h-8 min-h-8 cursor-pointer border-0 bg-panel px-3.5 text-[11px] text-muted outline-none transition-colors hover:bg-hover hover:text-ink focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-accent/25 disabled:cursor-not-allowed disabled:opacity-30', view === item.id && '!bg-accent/15 font-semibold !text-accent hover:!bg-accent/20')} key={item.id} title={`${item.title || item.label} (${formatShortcut(shortcuts[item.shortcutId])})`} aria-keyshortcuts={shortcutToAria(shortcuts[item.shortcutId])} aria-pressed={view === item.id} onClick={() => setView(item.id)}>{item.label}</button>)}</div>
    <div className="flex min-w-0 flex-1 items-center justify-center px-4">
      {centerContent}
    </div>
    {rightActions && <div className="flex shrink-0 items-center">{rightActions}</div>}
  </div>
}

export function CanvasZoomControls({disabled = false}: {disabled?: boolean}) {
  const zoom = useEditorStore(state => state.zoom)
  const setZoom = useEditorStore(state => state.setZoom)
  const shortcuts = useShortcutStore(state => state.shortcuts)
  const button = 'grid size-8 min-h-8 cursor-pointer place-items-center rounded-lg border border-transparent bg-transparent p-0 text-secondary outline-none transition-colors hover:bg-hover hover:text-ink disabled:cursor-not-allowed disabled:opacity-30 focus-visible:ring-2 focus-visible:ring-accent/30'
  return <div className="absolute left-3.5 top-3.5 z-20 flex items-center gap-1 rounded-xl border border-line-strong bg-popover p-1 shadow-soft backdrop-blur-xl" aria-label="画布缩放">
    <button className={button} disabled={disabled} title={`缩小 (${formatShortcut(shortcuts['zoom.out'])})`} aria-label="缩小画布" aria-keyshortcuts={shortcutToAria(shortcuts['zoom.out'])} onClick={() => setZoom(zoom / 1.15)}><ZoomOut size={16}/></button>
    <button className={button} disabled={disabled} title={`重置缩放 (${formatShortcut(shortcuts['zoom.reset'])})`} aria-label="重置画布缩放" aria-keyshortcuts={shortcutToAria(shortcuts['zoom.reset'])} onClick={() => setZoom(1)}><Focus size={16}/></button>
    <button className={button} disabled={disabled} title={`放大 (${formatShortcut(shortcuts['zoom.in'])})`} aria-label="放大画布" aria-keyshortcuts={shortcutToAria(shortcuts['zoom.in'])} onClick={() => setZoom(zoom * 1.15)}><ZoomIn size={16}/></button>
  </div>
}
