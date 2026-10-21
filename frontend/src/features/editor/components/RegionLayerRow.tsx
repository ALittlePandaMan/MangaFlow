import {Eye, EyeOff, Trash2, Type} from 'lucide-react'
import {memo} from 'react'
import type {MouseEvent as ReactMouseEvent} from 'react'
import type {TextRegion} from '../../../types'
import {RangeControl} from '../../../components/FormControls'
import {cn, compactIconButtonClass} from '../../../ui'
import {AutoResizeTextarea} from '../lib/inspectorFields'
import {RegionGeometryFields} from './RegionGeometryFields'

interface Props {
  region:TextRegion
  selected:boolean
  expanded:boolean
  disabled:boolean
  onSelect:(id:string,event:ReactMouseEvent<HTMLButtonElement>) => void
  onUpdate:(id:string,patch:Partial<TextRegion>) => void
  onDelete:() => void
}

export const RegionLayerRow=memo(function RegionLayerRow({region,selected,expanded,disabled,onSelect,onUpdate,onDelete}:Props) {
  const preview=region.translated_text.trim() || region.source_text.trim() || '空白文本'
  const percentage=Math.round(Math.max(0,Math.min(1,region.opacity))*100)
  const kind=region.bubble_id || region.region_type.startsWith('bubble') ? '对话' : '自由文本'
  return <article data-region-row={region.id} className={cn('inspector-text-row',selected && 'is-selected')}>
    <div className="inspector-layer-line">
      <button type="button" className="inspector-text-select" disabled={disabled} aria-label={`选择 ${region.region_key}：${preview}`} aria-pressed={selected} aria-expanded={expanded} onClick={event => onSelect(region.id,event)}>
        <Type className="inspector-layer-icon" size={16}/><span className="inspector-layer-title"><strong title={preview}>{preview}</strong><small><span className="font-mono">{region.region_key}</span><span aria-hidden="true"> · </span>{kind}</small></span>
      </button>
      {!expanded && <span className="inspector-layer-opacity">{percentage}%</span>}
      <button type="button" className={cn(compactIconButtonClass,'inspector-eye')} disabled={disabled} aria-label={`${region.region_key} ${region.visible ? '隐藏译文' : '显示译文'}`} title={region.visible ? '关闭：预览和导出时不显示译文' : '打开：预览和导出时显示译文'} onClick={() => onUpdate(region.id,{visible:!region.visible})}>{region.visible ? <Eye aria-hidden="true" size={16}/> : <EyeOff aria-hidden="true" size={16}/>}</button>
    </div>
    {expanded && <fieldset disabled={disabled} className="inspector-text-details">
      <div className="inspector-opacity-row"><span>不透明度</span><RangeControl ariaLabel="译文不透明度" min={0} max={100} value={percentage} onChange={value => onUpdate(region.id,{opacity:value/100})}/><output>{percentage}%</output><button type="button" className={cn(compactIconButtonClass,'icon-button-danger')} aria-label="删除当前区域" title="删除当前区域" onClick={onDelete}><Trash2 aria-hidden="true" size={16}/></button></div>
      <RegionGeometryFields region={region} onUpdate={onUpdate}/>
      {region.layout_data.ocr_geometry_stale === true && <p role="note" className="inspector-region-note">原文框范围已修改，请按新框重新 OCR，核对原文后再翻译。修复只同步框的位置，不会重新识字。</p>}
      <label>原文<AutoResizeTextarea value={region.source_text} onChange={value => onUpdate(region.id,{source_text:value})} placeholder="OCR 原文"/></label>
      <label>译文<AutoResizeTextarea value={region.translated_text} onChange={value => onUpdate(region.id,{translated_text:value})} placeholder="输入译文"/></label>
    </fieldset>}
  </article>
})
