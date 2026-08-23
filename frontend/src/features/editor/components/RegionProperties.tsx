import { Eraser, Eye, EyeOff, Languages, Layers3, Lock, LockOpen, Merge, Scan, ScanLine, ScanText, TextCursorInput, Trash2, X } from 'lucide-react'
import { useLayoutEffect, useRef } from 'react'
import type { TextRegion } from '../../../types'
import {buttonClass, cn, dangerButtonClass, iconButtonClass, inputClass, primaryButtonClass, textareaClass} from '../../../ui'
import {ColorControl, NumberControl, RangeControl, SelectControl} from '../../../components/FormControls'
import {ButtonLoading} from '../../../components/LoadingUI'
import {isPerspectiveQuad} from '../lib/perspectiveText'
import { useEditorStore } from '../store'

interface Props {
  region?: TextRegion
  selectedRegions: TextRegion[]
  selectedCount: number
  fontOptions: string[]
  busyAction?: string | null
  onUpdate: (id: string, patch: Partial<TextRegion>) => void
  onAction: (action: string, options?: Record<string, unknown>) => void
}

export function RegionProperties({region, selectedRegions, selectedCount, fontOptions, busyAction, onUpdate, onAction}: Props) {
  const {brushSize, brushHardness, setBrush, tool, select, view} = useEditorStore()
  const sectionClass = 'border-b border-line-subtle p-3'
  const headingClass = 'mb-3 flex items-center gap-2 font-mono text-[10px] font-medium uppercase leading-none tracking-[1.4px] text-muted before:h-[11px] before:w-0.5 before:rounded before:bg-accent/70'
  const fieldClass = 'mt-2 block min-w-0 text-[10px] text-secondary [&>*:last-child]:mt-1'
  const setCoordinate = (axis: 'x' | 'y', value: number) => {
    if (!region || !Number.isFinite(value)) return
    const translated = view === 'translated'
    const bbox = translated && region.translated_bbox?.length === 4 ? region.translated_bbox : region.bbox
    const polygon = translated && region.translated_polygon?.length >= 3 ? region.translated_polygon : region.polygon
    const dx = axis === 'x' ? value - bbox[0] : 0
    const dy = axis === 'y' ? value - bbox[1] : 0
    const nextBbox = [bbox[0] + dx, bbox[1] + dy, bbox[2], bbox[3]]
    const nextPolygon = polygon.map(point => [point[0] + dx, point[1] + dy])
    onUpdate(region.id, translated
      ? {translated_bbox: nextBbox, translated_polygon: nextPolygon}
      : {bbox: nextBbox, polygon: nextPolygon})
  }
  const coordinateBox = region && view === 'translated' && region.translated_bbox?.length === 4 ? region.translated_bbox : region?.bbox
  const canWarpPerspective = Boolean(region && isPerspectiveQuad(region.translated_polygon))
  const commonFontSize = selectedRegions.length && selectedRegions.every(item => item.font_size === selectedRegions[0].font_size)
    ? selectedRegions[0].font_size
    : undefined
  const updateSelectedFontSize = (fontSize: number) => {
    const next = Math.max(1, Math.min(300, fontSize))
    selectedRegions.forEach(item => onUpdate(item.id, {font_size: next}))
  }
  const stepSelectedFontSize = (direction: 1 | -1) => {
    selectedRegions.forEach(item => onUpdate(item.id, {font_size: Math.max(1, Math.min(300, item.font_size + direction))}))
  }
  return <aside className="flex min-h-0 flex-col overflow-y-auto border-l border-line-subtle bg-panel [scrollbar-color:#44443e_transparent] [scrollbar-width:thin] [&>*]:shrink-0">
    {(tool === 'mask-brush' || tool === 'mask-eraser') && <section className={sectionClass}><h3 className={headingClass}>Mask Paint</h3>
      <label className={fieldClass}>画笔大小 <output className="float-right font-mono text-[10px] text-secondary">{brushSize}px</output><RangeControl ariaLabel="画笔大小" min={2} max={160} value={brushSize} onChange={value => setBrush(value, brushHardness)}/></label>
      <label className={fieldClass}>硬度 <output className="float-right font-mono text-[10px] text-secondary">{Math.round(brushHardness * 100)}%</output><RangeControl ariaLabel="画笔硬度" min={0.05} max={1} step={0.05} value={brushHardness} onChange={value => setBrush(brushSize, value)}/></label>
      <div className="mt-3 flex gap-2"><button className={`${buttonClass} !min-h-8 flex-1 px-2 py-0 text-[10px]`} disabled={!!busyAction} onClick={() => onAction('dilate')}>{busyAction === 'dilate' ? <ButtonLoading label="处理中…"/> : '膨胀'}</button><button className={`${buttonClass} !min-h-8 flex-1 px-2 py-0 text-[10px]`} disabled={!!busyAction} onClick={() => onAction('erode')}>{busyAction === 'erode' ? <ButtonLoading label="处理中…"/> : '腐蚀'}</button><button className={`${buttonClass} !min-h-8 flex-1 px-2 py-0 text-[10px]`} disabled={!!busyAction} onClick={() => onAction('clear-mask')}>{busyAction === 'clear-mask' ? <ButtonLoading label="处理中…"/> : '清空'}</button></div>
    </section>}
    {selectedCount > 1 ? <div className="flex flex-1 flex-col justify-center gap-6 p-6">
      <div className="flex items-center gap-3 text-accent"><Layers3 size={28}/><span className="flex min-w-0 flex-col gap-1"><strong className="text-sm text-ink">已选择 {selectedCount} 个区域</strong><small className="text-[10px] leading-relaxed text-muted">继续点击可增减选择，方向键可微调位置</small></span></div>
      <div className="grid gap-2">
        <div className="rounded-xl border border-line-subtle bg-surface/60 p-3">
          <div className="mb-2 flex items-center justify-between gap-3"><span className="text-[10px] font-medium text-secondary">批量字号</span><small className="font-mono text-[9px] text-muted">{commonFontSize === undefined ? '混合字号' : `${commonFontSize}px`}</small></div>
          <NumberControl
            key={selectedRegions.map(item => item.id).sort().join(':')}
            compact ariaLabel="批量字号" value={commonFontSize} mixed={commonFontSize === undefined}
            placeholder="多种字号" min={1} max={300} step={1} disabled={!!busyAction}
            onChange={updateSelectedFontSize} onStep={stepSelectedFontSize}
          />
          <small className="mt-2 block text-[9px] leading-relaxed text-muted">输入数值可统一字号；加减按钮会让所选文字同步增减。</small>
        </div>
        <div className="grid grid-cols-2 gap-2">
          <button className={`${buttonClass} !min-h-[34px] px-2 text-[10px]`} disabled={!!busyAction} onClick={() => onAction('ocr')}>{busyAction === 'ocr' ? <ButtonLoading label="OCR 中…"/> : <><ScanText size={15}/>重新 OCR</>}</button>
          <button className={`${buttonClass} !min-h-[34px] px-2 text-[10px]`} disabled={!!busyAction} onClick={() => onAction('inpaint')}>{busyAction === 'inpaint' ? <ButtonLoading label="修复中…"/> : <><Eraser size={15}/>重新修复</>}</button>
          <button className={`${buttonClass} !min-h-[34px] px-2 text-[10px]`} disabled={!!busyAction} onClick={() => onAction('translate')}>{busyAction === 'translate' ? <ButtonLoading label="翻译中…"/> : <><Languages size={15}/>重新翻译</>}</button>
          <button className={`${buttonClass} !min-h-[34px] px-2 text-[10px]`} disabled={!!busyAction} onClick={() => onAction('render')}>{busyAction === 'render' ? <ButtonLoading label="排版中…"/> : <><TextCursorInput size={15}/>重新排版</>}</button>
        </div>
        <div className="my-1 h-px bg-line-subtle"/>
        <button className={`${primaryButtonClass} !min-h-[34px]`} disabled={!!busyAction} onClick={() => onAction('merge')}>{busyAction === 'merge' ? <ButtonLoading label="合并中…"/> : <><Merge size={16}/>合并区域</>}</button>
        <button className={`${dangerButtonClass} !min-h-[34px]`} disabled={!!busyAction} onClick={() => onAction('delete')}>{busyAction === 'delete' ? <ButtonLoading label="删除中…"/> : <><Trash2 size={16}/>删除所选</>}</button>
        <button className={`${buttonClass} !min-h-[34px]`} disabled={!!busyAction} onClick={() => select(null)}><X size={16}/>取消选择</button>
      </div>
    </div> : !region ? <div className="flex min-h-[260px] flex-1 flex-col items-center justify-center p-6 text-center text-muted"><Scan size={28}/><p className="text-[11px] leading-relaxed">选择一个文字区域以编辑属性</p></div> : <>
      <section className="flex h-[52px] shrink-0 items-center justify-between border-b border-line-subtle bg-surface px-3"><div className="flex items-center gap-2"><span className="font-mono text-xs font-semibold">{region.region_key}</span><i className="rounded border border-accent/25 bg-accent/10 px-2 py-1 font-mono text-[8px] font-medium not-italic text-accent">{Math.round(region.confidence * 100)}%</i></div><div className="flex gap-1"><button className={iconButtonClass} title={region.visible ? '关闭：预览和导出时不显示译文' : '打开：预览和导出时显示译文'} aria-label={region.visible ? '关闭区域显示' : '打开区域显示'} onClick={() => onAction('visibility')}>{region.visible ? <Eye size={16}/> : <EyeOff size={16}/>}</button><button className={iconButtonClass} title={region.locked ? '解除锁定' : '锁定区域'} aria-label={region.locked ? '解除锁定' : '锁定区域'} onClick={() => onUpdate(region.id, {locked: !region.locked})}>{region.locked ? <Lock size={16}/> : <LockOpen size={16}/>}</button></div></section>
      <section className={sectionClass}><h3 className={headingClass}>文本内容</h3>
        <label className={fieldClass}>原文<AutoResizeTextarea value={region.source_text} onChange={value => onUpdate(region.id, {source_text: value})} placeholder="OCR 原文" /></label>
        <label className={fieldClass}>译文<AutoResizeTextarea className="bg-accent/[.025] focus:bg-accent/[.04]" value={region.translated_text} onChange={value => onUpdate(region.id, {translated_text: value})} placeholder="输入译文" /></label>
        <div className="grid min-w-0 grid-cols-2 gap-2"><label className={fieldClass}>方向<SelectControl compact ariaLabel="文字方向" value={region.orientation} options={[{value:'vertical', label:'竖排'}, {value:'horizontal', label:'横排'}, {value:'rotated', label:'旋转'}]} onChange={value => onUpdate(region.id, {orientation:value as TextRegion['orientation']})}/></label><label className={fieldClass}>修复方式<input className={cn(inputClass, '!h-[34px] px-2 text-[11px]')} value="复杂（LaMa）" readOnly /></label></div>
      </section>
      <section className={sectionClass}><h3 className={headingClass}>{view === 'translated' ? '译文框坐标' : '原文框坐标'}</h3>
        <div className="grid min-w-0 grid-cols-2 gap-2">
          <label className={fieldClass}>X 坐标<NumberControl compact ariaLabel="X 坐标" step={1} disabled={region.locked} value={coordinateValue(coordinateBox![0])} onChange={value => setCoordinate('x', value)}/></label>
          <label className={fieldClass}>Y 坐标<NumberControl compact ariaLabel="Y 坐标" step={1} disabled={region.locked} value={coordinateValue(coordinateBox![1])} onChange={value => setCoordinate('y', value)}/></label>
        </div>
        <small className="mt-2 block font-mono text-[8px] text-muted">画布像素 · {coordinateValue(coordinateBox![2])} × {coordinateValue(coordinateBox![3])} px{region.locked ? ' · 已锁定' : ' · 方向键微调'}</small>
      </section>
      <section className={sectionClass}>
        <div className="mb-3 flex min-w-0 items-center justify-between gap-2">
          <h3 className={cn(headingClass, 'mb-0')}>文字样式</h3>
          <button
            type="button"
            className={cn('flex h-7 min-h-7 shrink-0 cursor-pointer items-center gap-1.5 rounded-lg border border-line bg-canvas px-2 text-[9px] font-medium text-secondary outline-none transition-colors hover:border-line-strong hover:bg-hover hover:text-ink focus-visible:ring-2 focus-visible:ring-accent/20 disabled:cursor-not-allowed disabled:opacity-35', region.perspective_warp && canWarpPerspective && 'border-accent/50 bg-accent/15 text-accent hover:bg-accent/20 hover:text-accent')}
            disabled={!canWarpPerspective && !region.perspective_warp}
            aria-pressed={Boolean(region.perspective_warp && canWarpPerspective)}
            aria-label="随形透视"
            title={canWarpPerspective ? '让译文沿四边形的四个角进行透视变形' : '随形透视仅支持有效的凸四边形译文框'}
            onClick={() => onUpdate(region.id, {perspective_warp: !region.perspective_warp})}
          ><ScanLine size={12}/><span>随形透视</span></button>
        </div>
        <label className={fieldClass}>字体<SelectControl compact ariaLabel="字体" value={region.font_family} options={[...new Set([...fontOptions, region.font_family])].filter(Boolean).map(font => ({value:font, label:font}))} onChange={value => onUpdate(region.id, {font_family:value})}/></label>
        <div className="grid min-w-0 grid-cols-3 gap-1"><label className={fieldClass}>字号<NumberControl compact ariaLabel="字号" value={region.font_size} min={1} max={300} step={1} onChange={value => onUpdate(region.id, {font_size:value})}/></label><label className={fieldClass}>行距<NumberControl compact ariaLabel="行距" value={region.line_spacing} min={0.5} max={3} step={0.05} onChange={value => onUpdate(region.id, {line_spacing:value})}/></label><label className={fieldClass}>字距<NumberControl compact ariaLabel="字距" value={region.character_spacing} min={-20} max={100} step={0.5} onChange={value => onUpdate(region.id, {character_spacing:value})}/></label></div>
        <div className="grid min-w-0 grid-cols-3 gap-1"><label className={fieldClass}>文字色<ColorControl compact ariaLabel="文字颜色" value={region.text_color} onChange={value => onUpdate(region.id, {text_color:value})}/></label><label className={fieldClass}>描边色<ColorControl compact ariaLabel="描边颜色" value={region.stroke_color} onChange={value => onUpdate(region.id, {stroke_color:value})}/></label><label className={fieldClass}>描边<NumberControl compact ariaLabel="描边宽度" value={region.stroke_width} min={0} max={20} step={0.5} onChange={value => onUpdate(region.id, {stroke_width:value})}/></label></div>
        <div className="grid min-w-0 grid-cols-2 gap-2"><label className={fieldClass}>对齐<SelectControl compact ariaLabel="文字对齐" value={region.alignment} options={[{value:'left', label:'左对齐'}, {value:'center', label:'居中'}, {value:'right', label:'右对齐'}]} onChange={value => onUpdate(region.id, {alignment:value as TextRegion['alignment']})}/></label><label className={fieldClass}>译文旋转<NumberControl compact ariaLabel="译文旋转角度" value={region.rotation} min={-180} max={180} step={1} onChange={value => onUpdate(region.id, {rotation:value})}/></label></div>
      </section>
    </>}
  </aside>
}

function coordinateValue(value: number): number {
  return Number(value.toFixed(2))
}

function AutoResizeTextarea({value, onChange, placeholder, className = ''}: {value: string, onChange: (value: string) => void, placeholder: string, className?: string}) {
  const textarea = useRef<HTMLTextAreaElement>(null)
  useLayoutEffect(() => {
    const element = textarea.current
    if (!element) return
    element.style.height = 'auto'
    element.style.height = `${Math.max(82, element.scrollHeight)}px`
  }, [value])
  return <textarea ref={textarea} className={cn(textareaClass, 'min-h-[82px] overflow-hidden text-[11px]', className)} value={value} onChange={event => onChange(event.target.value)} placeholder={placeholder}/>
}
