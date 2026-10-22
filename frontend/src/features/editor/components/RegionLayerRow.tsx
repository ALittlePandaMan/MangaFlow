import {t, useLocale} from '../../../i18n'
import {Eye, EyeOff, Lock, LockOpen, Trash2, Type} from 'lucide-react'
import {memo} from 'react'
import type {MouseEvent as ReactMouseEvent} from 'react'
import type {TextRegion} from '../../../types'
import {RangeControl} from '../../../components/FormControls'
import {cn, compactIconButtonClass} from '../../../ui'
import {AutoResizeTextarea} from '../lib/inspectorFields'

interface Props {
  region:TextRegion
  selected:boolean
  expanded:boolean
  disabled:boolean
  selectionDisabled?:boolean
  onSelect:(id:string,event:ReactMouseEvent<HTMLButtonElement>) => void
  onUpdate:(id:string,patch:Partial<TextRegion>) => void
  onDelete:() => void
}

export const RegionLayerRow=memo(function RegionLayerRow({region,selected,expanded,disabled,selectionDisabled=disabled,onSelect,onUpdate,onDelete}:Props) {
  useLocale()
  const preview=region.translated_text.trim() || region.source_text.trim() || t("空白文本")
  const percentage=Math.round(Math.max(0,Math.min(1,region.opacity))*100)
  const kind=region.bubble_id || region.region_type.startsWith('bubble') ? t("对话") : t("自由文本")
  return <article data-region-row={region.id} className={cn('inspector-text-row',selected && 'is-selected')}>
    <div className="inspector-layer-line">
      <button type="button" className="inspector-text-select" disabled={selectionDisabled} aria-label={t("选择 {{v0}}：{{v1}}", {v0: region.region_key, v1: preview})} aria-pressed={selected} aria-expanded={expanded} onClick={event => onSelect(region.id,event)}>
        <Type className="inspector-layer-icon" size={16}/><span className="inspector-layer-title"><strong title={preview}>{preview}</strong><small><span className="font-mono">{region.region_key}</span><span aria-hidden="true"> · </span>{kind}</small></span>
      </button>
      {!expanded && <span className="inspector-layer-opacity">{percentage}%</span>}
      {expanded && <button type="button" className={cn(compactIconButtonClass,'inspector-lock')} disabled={disabled} aria-label={region.locked ? t("解除锁定") : t("锁定区域")} title={region.locked ? t("解除锁定") : t("锁定区域")} onClick={() => onUpdate(region.id,{locked:!region.locked})}>{region.locked ? <Lock aria-hidden="true" size={16}/> : <LockOpen aria-hidden="true" size={16}/>}</button>}
      <button type="button" className={cn(compactIconButtonClass,'inspector-eye')} disabled={disabled} aria-label={`${region.region_key} ${region.visible ? t("隐藏译文") : t("显示译文")}`} title={region.visible ? t("关闭：预览和导出时不显示译文") : t("打开：预览和导出时显示译文")} onClick={() => onUpdate(region.id,{visible:!region.visible})}>{region.visible ? <Eye aria-hidden="true" size={16}/> : <EyeOff aria-hidden="true" size={16}/>}</button>
    </div>
    {expanded && <fieldset disabled={disabled} className="inspector-text-details">
      <div className="inspector-opacity-row"><span>{t("不透明度")}</span><RangeControl ariaLabel={t("译文不透明度")} min={0} max={100} value={percentage} onChange={value => onUpdate(region.id,{opacity:value/100})}/><output>{percentage}%</output><button type="button" className={cn(compactIconButtonClass,'icon-button-danger')} aria-label={t("删除当前区域")} title={t("删除当前区域")} onClick={onDelete}><Trash2 aria-hidden="true" size={16}/></button></div>
      {region.layout_data.ocr_geometry_stale === true && <p role="note" className="inspector-region-note">{t("原文框范围已修改，请按新框重新 OCR，核对原文后再翻译。修复只同步框的位置，不会重新识字。")}</p>}
      <label>{t("原文")}<AutoResizeTextarea value={region.source_text} onChange={value => onUpdate(region.id,{source_text:value})} placeholder={t("OCR 原文")}/></label>
      <label>{t("译文")}<AutoResizeTextarea value={region.translated_text} onChange={value => onUpdate(region.id,{translated_text:value})} placeholder={t("输入译文")}/></label>
    </fieldset>}
  </article>
})
