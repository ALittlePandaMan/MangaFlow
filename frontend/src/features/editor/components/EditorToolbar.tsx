import { BoxSelect, Brush, Eraser, Focus, LassoSelect, MousePointer2, PenTool, Redo2, Undo2, ZoomIn, ZoomOut } from 'lucide-react'
import type { Tool, ViewMode } from '../../../types'
import {cn} from '../../../ui'
import { useEditorStore } from '../store'

const tools: {id: Tool, title: string, shortcut: string, icon: React.ReactNode}[] = [
  {id: 'select', title: '选择 / 变换', shortcut: 'V', icon: <MousePointer2 size={17}/>},
  {id: 'rectangle', title: '矩形区域', shortcut: 'R', icon: <BoxSelect size={17}/>},
  {id: 'polygon', title: '多边形区域（点击首点闭合）', shortcut: 'P', icon: <PenTool size={17}/>},
  {id: 'lasso', title: '套索区域', shortcut: 'L', icon: <LassoSelect size={17}/>},
  {id: 'mask-brush', title: 'Mask 画笔', shortcut: 'B', icon: <Brush size={17}/>},
  {id: 'mask-eraser', title: 'Mask 橡皮擦', shortcut: 'E', icon: <Eraser size={17}/>},
]

const views: {id: ViewMode, label: string, shortcut: string, title?: string}[] = [
  {id: 'original', label: '原图', shortcut: '1'},
  {id: 'clean', label: '净图', shortcut: '2'},
  {id: 'translated', label: '译文', shortcut: '3'},
  {id: 'comparison', label: '对比', shortcut: '4', title: '原图 / 译文左右对比'},
]

export function EditorToolbar({ onUndo, onRedo, canUndo, canRedo, disabled = false, rightActions }: {onUndo: () => void, onRedo: () => void, canUndo: boolean, canRedo: boolean, disabled?: boolean, rightActions?: React.ReactNode}) {
  const {tool, setTool, view, setView, zoom, setZoom} = useEditorStore()
  const toolButton = 'grid size-8 min-h-8 cursor-pointer place-items-center rounded-md border border-transparent bg-transparent p-0 text-muted outline-none transition-colors hover:bg-hover hover:text-ink disabled:cursor-not-allowed disabled:opacity-30 focus-visible:ring-2 focus-visible:ring-accent/30'
  return <div className="flex h-11 items-center border-b border-line bg-surface px-3">
    <div className="flex gap-2">{tools.map(item => <button disabled={disabled} key={item.id} className={cn(toolButton, tool === item.id && '!border-accent !bg-accent !text-accent-ink shadow-[0_0_0_2px_rgb(16_211_163/.12)] hover:!bg-accent-hover hover:!text-accent-ink')} title={`${item.title} (${item.shortcut})`} aria-label={item.title} aria-keyshortcuts={item.shortcut} aria-pressed={tool === item.id} onClick={() => setTool(item.id)}>{item.icon}</button>)}</div>
    <span className="mx-3 h-5 border-l border-line" />
    <div className="flex gap-2"><button className={toolButton} disabled={disabled || !canUndo} onClick={onUndo} title="撤销 (Ctrl+Z)" aria-keyshortcuts="Control+Z Meta+Z"><Undo2 size={17}/></button><button className={toolButton} disabled={disabled || !canRedo} onClick={onRedo} title="重做 (Ctrl+Y / Ctrl+Shift+Z)" aria-keyshortcuts="Control+Y Meta+Y Control+Shift+Z Meta+Shift+Z"><Redo2 size={17}/></button></div>
    <span className="mx-3 h-5 border-l border-line" />
    <div className="flex overflow-hidden rounded-md border border-line bg-panel">{views.map(item => <button disabled={disabled} className={cn('h-[30px] min-h-[30px] cursor-pointer border-0 bg-panel px-3 text-[10px] text-muted outline-none transition-colors hover:bg-hover hover:text-ink disabled:cursor-not-allowed disabled:opacity-30', view === item.id && '!bg-hover font-semibold !text-ink')} key={item.id} title={`${item.title || item.label} (${item.shortcut})`} aria-keyshortcuts={item.shortcut} aria-pressed={view === item.id} onClick={() => setView(item.id)}>{item.label}</button>)}</div>
    <div className="flex-1" />
    <div className="flex gap-2"><button className={toolButton} disabled={disabled} title="缩小 (-)" aria-keyshortcuts="-" onClick={() => setZoom(zoom / 1.15)}><ZoomOut size={16}/></button><button className={toolButton} disabled={disabled} onClick={() => setZoom(1)} title="重置缩放 (0)" aria-keyshortcuts="0"><Focus size={16}/></button><button className={toolButton} disabled={disabled} title="放大 (+)" aria-keyshortcuts="+" onClick={() => setZoom(zoom * 1.15)}><ZoomIn size={16}/></button></div>
    {rightActions && <><span className="mx-3 h-5 border-l border-line"/><div className="flex shrink-0 items-center">{rightActions}</div></>}
  </div>
}
