import {t, useLocale} from '../../../i18n'
import { BoxSelect, Eraser, Hand, LassoSelect, MousePointer2, Paintbrush, PenTool, Redo2, Undo2 } from 'lucide-react'
import type { Tool, ViewMode } from '../../../types'
import {cn, compactIconButtonClass} from '../../../ui'
import {formatShortcut, shortcutToAria, type ShortcutId, useShortcutStore} from '../../shortcuts/store'
import { useEditorStore } from '../store'

const tools: {id: Tool, title: string, shortcutId: ShortcutId, icon: React.ReactNode}[] = [
  {id: 'pan', get title(){return t("拖拽画布")}, shortcutId: 'tool.pan', icon: <Hand size={17}/>},
  {id: 'select', get title(){return t("选择 / 变换")}, shortcutId: 'tool.select', icon: <MousePointer2 size={17}/>},
  {id: 'rectangle', get title(){return t("矩形区域")}, shortcutId: 'tool.rectangle', icon: <BoxSelect size={17}/>},
  {id: 'polygon', get title(){return t("多边形区域（点击首点闭合）")}, shortcutId: 'tool.polygon', icon: <PenTool size={17}/>},
  {id: 'lasso', get title(){return t("套索区域")}, shortcutId: 'tool.lasso', icon: <LassoSelect size={17}/>},
]

const maskTools: {id: Tool, title: string, icon: React.ReactNode}[] = [
  {id: 'pan', get title(){return t("拖拽蒙版画布")}, icon: <Hand size={17}/>},
  {id: 'select', get title(){return t("选择蒙版区域")}, icon: <MousePointer2 size={17}/>},
  {id: 'brush', get title(){return t("蒙版画笔")}, icon: <Paintbrush size={17}/>},
  {id: 'eraser', get title(){return t("蒙版橡皮擦")}, icon: <Eraser size={17}/>},
]

const views: {id: ViewMode, label: string, shortcutId?: ShortcutId, title?: string}[] = [
  {id: 'original', get label(){return t("原图")}, shortcutId: 'view.original'},
  {id: 'clean', get label(){return t("净图")}, shortcutId: 'view.clean'},
  {id: 'translated', get label(){return t("译文",{context:'view'})}, shortcutId: 'view.translated'},
  {id: 'comparison', get label(){return t("对比")}, shortcutId: 'view.comparison', get title(){return t("原图 / 译文左右对比")}},
  {id: 'mask', get label(){return t("蒙版")}, get title(){return t("只显示修复蒙版，在画布上绘制或擦除")}},
]

export function EditorToolbar({ onUndo, onRedo, canUndo, canRedo, disabled = false, readOnly = false, onViewChange }: {onUndo: () => void, onRedo: () => void, canUndo: boolean, canRedo: boolean, disabled?: boolean, readOnly?: boolean, onViewChange?: (view: ViewMode) => void}) {
  useLocale()
  const tool = useEditorStore(state => state.tool)
  const setTool = useEditorStore(state => state.setTool)
  const maskOperation = useEditorStore(state => state.maskOperation)
  const setMaskOperation = useEditorStore(state => state.setMaskOperation)
  const view = useEditorStore(state => state.view)
  const setView = useEditorStore(state => state.setView)
  const shortcuts = useShortcutStore(state => state.shortcuts)
  const toolButton = cn(compactIconButtonClass,'editor-tool-button')
  return <div className="editor-toolbar border-b border-line-subtle bg-panel" role="toolbar" aria-label={t("编辑器顶部工具栏")}>
    <div className={cn('editor-tools flex items-center', view === 'mask' && 'editor-tools-mask')}>
    {view === 'mask' ? <div className="flex gap-1.5" aria-label={t("修复蒙版工具")}>{maskTools.map(item => {
      const painting = item.id === 'brush' || item.id === 'eraser'
      const active = painting ? tool !== 'pan' && tool !== 'select' && maskOperation === item.id : tool === item.id
      return <button key={item.id} className={cn(toolButton, active && '!border-accent-line !bg-accent-soft !text-accent-soft-ink')} disabled={disabled || readOnly && item.id !== 'pan'} aria-label={item.title} aria-pressed={active} title={item.title} onClick={() => painting ? setMaskOperation(item.id as 'brush' | 'eraser') : setTool(item.id)}>{item.icon}</button>
    })}</div>
      : <div className="flex gap-1.5" aria-label={t("普通编辑工具")}>{tools.map(item => <button disabled={disabled || readOnly && item.id !== 'pan' && item.id !== 'select'} key={item.id} className={cn(toolButton, tool === item.id && '!border-accent-line !bg-accent-soft !text-accent-soft-ink hover:!bg-accent-soft-hover')} title={`${item.title} (${formatShortcut(shortcuts[item.shortcutId])})`} aria-label={item.title} aria-keyshortcuts={shortcutToAria(shortcuts[item.shortcutId])} aria-pressed={tool === item.id} onClick={() => setTool(item.id)}>{item.icon}</button>)}</div>}
    <span className="mx-3 h-5 border-l border-line" />
    <div className="flex gap-1.5"><button className={toolButton} disabled={disabled || readOnly || !canUndo} onClick={onUndo} title={t("撤销 ({{v0}})", {v0: formatShortcut(shortcuts['edit.undo'])})} aria-label={t("撤销")} aria-keyshortcuts={shortcutToAria(shortcuts['edit.undo'])}><Undo2 size={17}/></button><button className={toolButton} disabled={disabled || readOnly || !canRedo} onClick={onRedo} title={t("重做 ({{v0}})", {v0: formatShortcut(shortcuts['edit.redo'])})} aria-label={t("重做")} aria-keyshortcuts={shortcutToAria(shortcuts['edit.redo'])}><Redo2 size={17}/></button></div>
    </div>
    <div className="editor-view-tabs flex overflow-hidden rounded-lg border border-line bg-panel">{views.map(item => <button disabled={disabled || readOnly && item.id === 'mask'} className={cn('h-8 min-h-8 cursor-pointer border-0 bg-panel px-3.5 text-[12px] text-muted outline-none transition-colors hover:bg-hover hover:text-ink focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-accent/25 disabled:cursor-not-allowed disabled:opacity-30', view === item.id && '!bg-accent-soft font-semibold !text-accent-soft-ink hover:!bg-accent-soft-hover')} key={item.id} title={`${item.title || item.label}${item.shortcutId ? ` (${formatShortcut(shortcuts[item.shortcutId])})` : ''}`} aria-keyshortcuts={item.shortcutId ? shortcutToAria(shortcuts[item.shortcutId]) : undefined} aria-pressed={view === item.id} onClick={() => (onViewChange || setView)(item.id)}>{item.label}</button>)}</div>
  </div>
}
