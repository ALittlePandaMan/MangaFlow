import { Eraser, Eye, EyeOff, Languages, Layers3, Lock, LockOpen, Merge, Scan, ScanLine, ScanText, TextCursorInput, Trash2, X } from 'lucide-react'
import { useLayoutEffect, useRef } from 'react'
import type { TextRegion } from '../../../types'
import {buttonClass, cn, dangerButtonClass, inputClass, primaryButtonClass, scrollbarClass, textareaClass} from '../../../ui'
import {ColorControl, NumberControl, SelectControl} from '../../../components/FormControls'
import {ButtonLoading} from '../../../components/LoadingUI'
import {formatShortcut, shortcutToAria, useShortcutStore} from '../../shortcuts/store'
import {isPerspectiveQuad} from '../lib/perspectiveText'
import { useEditorStore } from '../store'

const regionIconButtonClass = cn(buttonClass, '!size-8 !min-h-8 !p-0')

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
  const {select, view} = useEditorStore()
  const shortcuts = useShortcutStore(state => state.shortcuts)
  const sectionClass = 'border-b border-line-subtle p-4'
  const headingClass = 'mb-3.5 flex items-center gap-2 font-mono text-[11px] font-semibold uppercase leading-none tracking-[1.3px] text-muted before:h-3 before:w-0.5 before:rounded before:bg-accent/70'
  const fieldClass = 'mt-2.5 block min-w-0 text-[11px] font-medium text-secondary [&>*:last-child]:mt-1.5'
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
  const coordinatePolygon = region && view === 'translated' && region.translated_polygon?.length >= 3 ? region.translated_polygon : region?.polygon
  const coordinateRotation = coordinatePolygon?.length ? coordinateValue(polygonRotation(coordinatePolygon)) : 0
  const setCoordinateRotation = (value: number) => {
    if (!region || !coordinatePolygon?.length || !Number.isFinite(value)) return
    const delta = normalizedAngle(value - polygonRotation(coordinatePolygon))
    if (Math.abs(delta) < .001) return
    const nextPolygon = rotatePolygon(coordinatePolygon, delta)
    const nextBbox = polygonBbox(nextPolygon)
    onUpdate(region.id, view === 'translated'
      ? {translated_polygon: nextPolygon, translated_bbox: nextBbox, rotation: normalizedAngle(region.rotation + delta)}
      : {polygon: nextPolygon, bbox: nextBbox})
  }
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
  return <aside className={cn('flex min-h-0 flex-col overflow-y-auto border-l border-line-subtle bg-panel [&>*]:shrink-0', scrollbarClass)}>
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
          <button className={`${buttonClass} !min-h-[34px] px-2 text-[10px]`} disabled={!!busyAction} title={`所选区域重新 OCR (${formatShortcut(shortcuts['region.ocr'])})`} aria-keyshortcuts={shortcutToAria(shortcuts['region.ocr'])} onClick={() => onAction('ocr')}>{busyAction === 'ocr' ? <ButtonLoading label="OCR 中…"/> : <><ScanText size={15}/>重新 OCR</>}</button>
          <button className={`${buttonClass} !min-h-[34px] px-2 text-[10px]`} disabled={!!busyAction} title={`所选区域重新修复 (${formatShortcut(shortcuts['region.inpaint'])})`} aria-keyshortcuts={shortcutToAria(shortcuts['region.inpaint'])} onClick={() => onAction('inpaint')}>{busyAction === 'inpaint' ? <ButtonLoading label="修复中…"/> : <><Eraser size={15}/>重新修复</>}</button>
          <button className={`${buttonClass} !min-h-[34px] px-2 text-[10px]`} disabled={!!busyAction} title={`所选区域重新翻译 (${formatShortcut(shortcuts['region.translate'])})`} aria-keyshortcuts={shortcutToAria(shortcuts['region.translate'])} onClick={() => onAction('translate')}>{busyAction === 'translate' ? <ButtonLoading label="翻译中…"/> : <><Languages size={15}/>重新翻译</>}</button>
          <button className={`${buttonClass} !min-h-[34px] px-2 text-[10px]`} disabled={!!busyAction} title={`所选区域重新排版 (${formatShortcut(shortcuts['region.render'])})`} aria-keyshortcuts={shortcutToAria(shortcuts['region.render'])} onClick={() => onAction('render')}>{busyAction === 'render' ? <ButtonLoading label="排版中…"/> : <><TextCursorInput size={15}/>重新排版</>}</button>
        </div>
        <div className="my-1 h-px bg-line-subtle"/>
        <button className={`${primaryButtonClass} !min-h-[34px]`} disabled={!!busyAction} title={`合并所选 (${formatShortcut(shortcuts['region.merge'])})`} aria-keyshortcuts={shortcutToAria(shortcuts['region.merge'])} onClick={() => onAction('merge')}>{busyAction === 'merge' ? <ButtonLoading label="合并中…"/> : <><Merge size={16}/>合并区域</>}</button>
        <button className={`${dangerButtonClass} !min-h-[34px]`} disabled={!!busyAction} title={`删除所选 (${formatShortcut(shortcuts['region.delete'])})`} aria-keyshortcuts={shortcutToAria(shortcuts['region.delete'])} onClick={() => onAction('delete')}>{busyAction === 'delete' ? <ButtonLoading label="删除中…"/> : <><Trash2 size={16}/>删除所选</>}</button>
        <button className={`${buttonClass} !min-h-[34px]`} disabled={!!busyAction} title={`取消选择 (${formatShortcut(shortcuts['region.cancelSelection'])})`} aria-keyshortcuts={shortcutToAria(shortcuts['region.cancelSelection'])} onClick={() => select(null)}><X size={16}/>取消选择</button>
      </div>
    </div> : !region ? <div className="flex min-h-[260px] flex-1 flex-col items-center justify-center p-8 text-center text-muted"><Scan size={30}/><p className="mt-3 text-[12px] leading-relaxed">选择一个文字区域以编辑属性</p></div> : <>
      <section className="flex h-12 shrink-0 items-center justify-between border-b border-line-subtle bg-surface px-3"><span className="font-mono text-xs font-semibold">{region.region_key}</span><div className="flex gap-1"><button className={regionIconButtonClass} title={region.visible ? '关闭：预览和导出时不显示译文' : '打开：预览和导出时显示译文'} aria-label={region.visible ? '关闭区域显示' : '打开区域显示'} onClick={() => onAction('visibility')}>{region.visible ? <Eye aria-hidden="true" size={16}/> : <EyeOff aria-hidden="true" size={16}/>}</button><button className={regionIconButtonClass} title={region.locked ? '解除锁定' : '锁定区域'} aria-label={region.locked ? '解除锁定' : '锁定区域'} onClick={() => onUpdate(region.id, {locked: !region.locked})}>{region.locked ? <Lock aria-hidden="true" size={16}/> : <LockOpen aria-hidden="true" size={16}/>}</button></div></section>
      <section className={sectionClass}><h3 className={headingClass}>文本内容</h3>
        <label className={fieldClass}>原文<AutoResizeTextarea value={region.source_text} onChange={value => onUpdate(region.id, {source_text: value})} placeholder="OCR 原文" /></label>
        <label className={fieldClass}>译文<AutoResizeTextarea className="bg-accent/[.025] focus:bg-accent/[.04]" value={region.translated_text} onChange={value => onUpdate(region.id, {translated_text: value})} placeholder="输入译文" /></label>
        <div className="grid min-w-0 grid-cols-2 gap-2"><label className={fieldClass}>方向<SelectControl compact ariaLabel="文字方向" value={region.orientation} options={[{value:'vertical', label:'竖排'}, {value:'horizontal', label:'横排'}, {value:'rotated', label:'旋转'}]} onChange={value => onUpdate(region.id, {orientation:value as TextRegion['orientation']})}/></label><label className={fieldClass}>修复方式<input className={cn(inputClass, '!h-[34px] px-2 text-[11px]')} value="复杂（LaMa）" readOnly /></label></div>
      </section>
      <section className={sectionClass}><h3 className={headingClass}>{view === 'translated' ? '译文框坐标' : '原文框坐标'}</h3>
        <div className="grid min-w-0 grid-cols-3 gap-1.5">
          <label className={fieldClass}>X 坐标<NumberControl compact ariaLabel="X 坐标" step={1} disabled={region.locked} value={coordinateValue(coordinateBox![0])} onChange={value => setCoordinate('x', value)}/></label>
          <label className={fieldClass}>Y 坐标<NumberControl compact ariaLabel="Y 坐标" step={1} disabled={region.locked} value={coordinateValue(coordinateBox![1])} onChange={value => setCoordinate('y', value)}/></label>
          <label className={fieldClass}>旋转角度<NumberControl compact ariaLabel={`${view === 'translated' ? '译文框' : '原文框'}旋转角度（度）`} min={-180} max={180} step={1} disabled={region.locked} value={coordinateRotation} onChange={setCoordinateRotation}/></label>
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

function normalizedAngle(degrees: number): number {
  let angle = degrees % 360
  if (angle > 180) angle -= 360
  if (angle < -180) angle += 360
  return Math.abs(angle) < .001 ? 0 : angle
}

function polygonRotation(polygon: number[][]): number {
  for (let index = 0; index < polygon.length; index += 1) {
    const current = polygon[index]
    const next = polygon[(index + 1) % polygon.length]
    const dx = next[0] - current[0]
    const dy = next[1] - current[1]
    if (Math.hypot(dx, dy) > .001) return normalizedAngle(Math.atan2(dy, dx) * 180 / Math.PI)
  }
  return 0
}

function rotatePolygon(polygon: number[][], degrees: number): number[][] {
  const center = polygon.reduce((sum, point) => [sum[0] + point[0], sum[1] + point[1]], [0, 0])
    .map(total => total / polygon.length)
  const radians = degrees * Math.PI / 180
  const cosine = Math.cos(radians)
  const sine = Math.sin(radians)
  return polygon.map(point => {
    const x = point[0] - center[0]
    const y = point[1] - center[1]
    return [center[0] + x * cosine - y * sine, center[1] + x * sine + y * cosine]
  })
}

function polygonBbox(polygon: number[][]): number[] {
  const xs = polygon.map(point => point[0])
  const ys = polygon.map(point => point[1])
  const minX = Math.min(...xs)
  const minY = Math.min(...ys)
  return [minX, minY, Math.max(...xs) - minX, Math.max(...ys) - minY]
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
