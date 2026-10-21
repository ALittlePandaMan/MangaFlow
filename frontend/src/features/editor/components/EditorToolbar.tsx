import { BoxSelect, Eraser, Focus, Hand, LassoSelect, MousePointer2, Paintbrush, PenTool, Redo2, Undo2, ZoomIn, ZoomOut } from 'lucide-react'
import type { Tool, ViewMode } from '../../../types'
import {cn, compactIconButtonClass} from '../../../ui'
import {formatShortcut, shortcutToAria, type ShortcutId, useShortcutStore} from '../../shortcuts/store'
import { useEditorStore } from '../store'

const tools: {id: Tool, title: string, shortcutId: ShortcutId, icon: React.ReactNode}[] = [
  {id: 'pan', title: '拖拽画布', shortcutId: 'tool.pan', icon: <Hand size={17}/>},
  {id: 'select', title: '选择 / 变换', shortcutId: 'tool.select', icon: <MousePointer2 size={17}/>},
  {id: 'rectangle', title: '矩形区域', shortcutId: 'tool.rectangle', icon: <BoxSelect size={17}/>},
  {id: 'polygon', title: '多边形区域（点击首点闭合）', shortcutId: 'tool.polygon', icon: <PenTool size={17}/>},
  {id: 'lasso', title: '套索区域', shortcutId: 'tool.lasso', icon: <LassoSelect size={17}/>},
]

const maskTools: {id: Tool, title: string, icon: React.ReactNode}[] = [
  {id: 'pan', title: '拖拽蒙版画布', icon: <Hand size={17}/>},
  {id: 'select', title: '选择蒙版区域', icon: <MousePointer2 size={17}/>},
  {id: 'brush', title: '蒙版画笔', icon: <Paintbrush size={17}/>},
  {id: 'eraser', title: '蒙版橡皮擦', icon: <Eraser size={17}/>},
]

const views: {id: ViewMode, label: string, shortcutId?: ShortcutId, title?: string}[] = [
  {id: 'original', label: '原图', shortcutId: 'view.original'},
  {id: 'clean', label: '净图', shortcutId: 'view.clean'},
  {id: 'translated', label: '译文', shortcutId: 'view.translated'},
  {id: 'comparison', label: '对比', shortcutId: 'view.comparison', title: '原图 / 译文左右对比'},
  {id: 'mask', label: '蒙版', title: '只显示修复蒙版，在画布上绘制或擦除'},
]

export function EditorToolbar({ onUndo, onRedo, canUndo, canRedo, disabled = false, readOnly = false, centerContent, onViewChange }: {onUndo: () => void, onRedo: () => void, canUndo: boolean, canRedo: boolean, disabled?: boolean, readOnly?: boolean, centerContent?: React.ReactNode, onViewChange?: (view: ViewMode) => void}) {
  const tool = useEditorStore(state => state.tool)
  const setTool = useEditorStore(state => state.setTool)
  const maskOperation = useEditorStore(state => state.maskOperation)
  const setMaskOperation = useEditorStore(state => state.setMaskOperation)
  const view = useEditorStore(state => state.view)
  const setView = useEditorStore(state => state.setView)
  const shortcuts = useShortcutStore(state => state.shortcuts)
  const toolButton = cn(compactIconButtonClass,'editor-tool-button')
  return <div className="editor-toolbar border-b border-line-subtle bg-panel" role="toolbar" aria-label="编辑器顶部工具栏">
    <div className={cn('editor-tools flex items-center', view === 'mask' && 'editor-tools-mask')}>
    {view === 'mask' ? <div className="flex gap-1.5" aria-label="修复蒙版工具">{maskTools.map(item => {
      const painting = item.id === 'brush' || item.id === 'eraser'
      const active = painting ? tool !== 'pan' && tool !== 'select' && maskOperation === item.id : tool === item.id
      return <button key={item.id} className={cn(toolButton, active && '!border-accent-line !bg-accent-soft !text-accent-soft-ink')} disabled={disabled || readOnly && item.id !== 'pan'} aria-label={item.title} aria-pressed={active} title={item.title} onClick={() => painting ? setMaskOperation(item.id as 'brush' | 'eraser') : setTool(item.id)}>{item.icon}</button>
    })}</div>
      : <div className="flex gap-1.5" aria-label="普通编辑工具">{tools.map(item => <button disabled={disabled || readOnly && item.id !== 'pan'} key={item.id} className={cn(toolButton, tool === item.id && '!border-accent-line !bg-accent-soft !text-accent-soft-ink hover:!bg-accent-soft-hover')} title={`${item.title} (${formatShortcut(shortcuts[item.shortcutId])})`} aria-label={item.title} aria-keyshortcuts={shortcutToAria(shortcuts[item.shortcutId])} aria-pressed={tool === item.id} onClick={() => setTool(item.id)}>{item.icon}</button>)}</div>}
    <span className="mx-3 h-5 border-l border-line" />
    <div className="flex gap-1.5"><button className={toolButton} disabled={disabled || readOnly || !canUndo} onClick={onUndo} title={`撤销 (${formatShortcut(shortcuts['edit.undo'])})`} aria-label="撤销" aria-keyshortcuts={shortcutToAria(shortcuts['edit.undo'])}><Undo2 size={17}/></button><button className={toolButton} disabled={disabled || readOnly || !canRedo} onClick={onRedo} title={`重做 (${formatShortcut(shortcuts['edit.redo'])})`} aria-label="重做" aria-keyshortcuts={shortcutToAria(shortcuts['edit.redo'])}><Redo2 size={17}/></button></div>
    </div>
    <div className="editor-feedback flex min-w-0 items-center justify-center">
      {centerContent}
    </div>
    <div className="editor-view-tabs flex overflow-hidden rounded-lg border border-line bg-panel">{views.map(item => <button disabled={disabled || readOnly && item.id === 'mask'} className={cn('h-8 min-h-8 cursor-pointer border-0 bg-panel px-3.5 text-[11px] text-muted outline-none transition-colors hover:bg-hover hover:text-ink focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-accent/25 disabled:cursor-not-allowed disabled:opacity-30', view === item.id && '!bg-accent-soft font-semibold !text-accent-soft-ink hover:!bg-accent-soft-hover')} key={item.id} title={`${item.title || item.label}${item.shortcutId ? ` (${formatShortcut(shortcuts[item.shortcutId])})` : ''}`} aria-keyshortcuts={item.shortcutId ? shortcutToAria(shortcuts[item.shortcutId]) : undefined} aria-pressed={view === item.id} onClick={() => (onViewChange || setView)(item.id)}>{item.label}</button>)}</div>
  </div>
}

export function CanvasZoomControls({disabled = false}: {disabled?: boolean}) {
  const zoom = useEditorStore(state => state.zoom)
  const setZoom = useEditorStore(state => state.setZoom)
  const resetView = useEditorStore(state => state.resetView)
  const shortcuts = useShortcutStore(state => state.shortcuts)
  const button = compactIconButtonClass
  return <div className="absolute left-3.5 top-3.5 z-20 flex flex-col items-center gap-1 rounded-xl border border-line-strong bg-popover p-1 shadow-soft backdrop-blur-xl" aria-label="画布缩放">
    <button className={button} disabled={disabled} title={`缩小 (${formatShortcut(shortcuts['zoom.out'])})`} aria-label="缩小画布" aria-keyshortcuts={shortcutToAria(shortcuts['zoom.out'])} onClick={() => setZoom(zoom / 1.15)}><ZoomOut size={16}/></button>
    <button className={button} disabled={disabled} title={`适合画布并居中 (${formatShortcut(shortcuts['zoom.reset'])})`} aria-label="重置画布缩放" aria-keyshortcuts={shortcutToAria(shortcuts['zoom.reset'])} onClick={resetView}><Focus size={16}/></button>
    <button className={button} disabled={disabled} title={`放大 (${formatShortcut(shortcuts['zoom.in'])})`} aria-label="放大画布" aria-keyshortcuts={shortcutToAria(shortcuts['zoom.in'])} onClick={() => setZoom(zoom * 1.15)}><ZoomIn size={16}/></button>
  </div>
}
