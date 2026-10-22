import {t, useLocale} from '../../../i18n'
import {AlignLeft, AlignCenter, AlignRight, ChevronDown, Type} from 'lucide-react'
import {useId, useMemo, useState} from 'react'
import type {TextRegion} from '../../../types'
import {ColorControl, NumberControl, SelectControl} from '../../../components/FormControls'
import {cn} from '../../../ui'
import {isPerspectiveQuad} from '../lib/perspectiveText'
import {coordinateValue, currentFontSize} from '../lib/inspectorFields'
import {useFontRevision} from '../lib/fontRevision'
import {effectiveTextRotation} from '../lib/textRotation'
import {translatedTextBox, translateText} from '../lib/textTransform'

interface Props {
  regions:TextRegion[]
  fontOptions:string[]
  busy:boolean
  onChange:(changes:Array<{id:string;patch:Partial<TextRegion>}>) => void
}

function common<K extends keyof TextRegion>(regions:TextRegion[], field:K):TextRegion[K] | undefined {
  const value=regions[0]?.[field]
  return regions.every(region => region[field] === value) ? value : undefined
}

export function TextTypesettingPanel({regions,fontOptions,busy,onChange}:Props) {
  useLocale()
  const [expanded,setExpanded]=useState(true)
  const fieldsId=useId()
  const disabled=busy || !regions.length, multiple=regions.length>1
  const fontRevision=useFontRevision()
  const sizes=useMemo(() => {void fontRevision; return regions.map(currentFontSize)},[regions,fontRevision])
  const size=sizes.length && sizes.every(value => value === sizes[0]) ? sizes[0] : undefined
  const font=common(regions,'font_family'), weight=common(regions,'font_weight')
  const alignment=common(regions,'alignment'), direction=common(regions,'orientation')
  const perspective=common(regions,'perspective_warp')
  const movable=regions.filter(region => !region.locked)
  const transformDisabled=disabled || !movable.length
  const boxes=movable.map(translatedTextBox)
  const x=boxes.length ? Math.min(...boxes.map(box => box[0])) : undefined
  const y=boxes.length ? Math.min(...boxes.map(box => box[1])) : undefined
  const rotations=movable.map(effectiveTextRotation)
  const rotation=rotations.every(value => value === rotations[0]) ? rotations[0] : undefined
  const canWarp=!!regions.length && regions.every(region => isPerspectiveQuad(region.translated_polygon))
  const change=(patch:Partial<TextRegion>) => {if(!disabled) onChange(regions.map(region => ({id:region.id,patch})))}
  const setSize=(value:number) => change({font_size:Math.max(1,Math.min(512,value)),auto_font_size:false})
  const stepSize=(direction:1|-1) => onChange(regions.map((region,index) => ({id:region.id,patch:{font_size:Math.max(1,Math.min(512,sizes[index]+direction)),auto_font_size:false}})))
  const setPosition=(axis:'x'|'y',value:number) => {
    const origin=axis === 'x' ? x : y
    if(transformDisabled || origin === undefined || !Number.isFinite(value) || value === origin) return
    onChange(movable.map(region => ({id:region.id,patch:translateText(region,axis === 'x' ? value-origin : 0,axis === 'y' ? value-origin : 0)})))
  }
  const setRotation=(value:number) => {
    if(!transformDisabled) onChange(movable.map(region => ({id:region.id,patch:{rotation:value}})))
  }
  const stepRotation=(direction:1|-1) => {
    if(!transformDisabled) onChange(movable.map((region,index) => ({id:region.id,patch:{rotation:Math.max(-180,Math.min(180,rotations[index]+direction))}})))
  }
  return <section className="inspector-typesetting" aria-label={t("排版设置")}>
    <h3 className="inspector-section-heading"><button type="button" className="inspector-section-toggle" aria-expanded={expanded} aria-controls={fieldsId} onClick={() => setExpanded(value => !value)}><Type size={15}/><span>{t("排版")}</span>{multiple && <small>{t("已选择")}{' '}{regions.length} {' '}{t("个区域")}</small>}<ChevronDown size={15} className="inspector-section-chevron"/></button></h3>
    <fieldset id={fieldsId} hidden={!expanded} key={regions.map(region => region.id).join(':')} disabled={disabled} className="inspector-typesetting-fields">
      <div className="inspector-font-row">
        <label>{t("字体")}<SelectControl compact ariaLabel={t("字体")} value={font || ''} placeholder={multiple ? t("多种字体") : t("选择文本")} disabled={disabled} options={[...new Set([...fontOptions,...regions.map(region => region.font_family)])].filter(Boolean).map(value => ({value,label:value}))} onChange={value => change({font_family:value})}/></label>
        <label className="inspector-color-control">{t("颜色")}<ColorControl compact ariaLabel={t("文字颜色")} value={common(regions,'text_color') || '#111111'} disabled={disabled} onChange={value => change({text_color:value})}/></label>
      </div>
      <div className="inspector-size-row">
        <label>{t("字号")}<NumberControl commitOnBlur key={regions.map(region => region.id).join(':')} compact ariaLabel={multiple ? t("批量字号") : t("字号")} value={size} mixed={multiple && size === undefined} placeholder={multiple ? t("多种字号") : '—'} disabled={disabled} min={1} max={512} step={1} onChange={setSize} onStep={stepSize}/></label>
        <label>{t("字重")}<SelectControl compact ariaLabel={t("字重")} value={weight === undefined ? '' : String(weight)} placeholder={multiple ? t("混合") : '—'} disabled={disabled} options={[100,200,300,400,500,600,700,800,900].map(value => ({value:String(value),label:String(value)}))} onChange={value => change({font_weight:Number(value)})}/></label>
      </div>
      <div className="inspector-alignment-row">
        <label>{t("对齐")}<span className="inspector-alignment-buttons">{([['left',t("左对齐"),AlignLeft],['center',t("居中"),AlignCenter],['right',t("右对齐"),AlignRight]] as const).map(([value,label,Icon]) => <button type="button" key={value} aria-label={`${multiple ? t("所选文字") : t("文字")}${label}`} aria-pressed={alignment === value} title={label} disabled={disabled} className={cn(alignment === value && 'is-active')} onClick={() => change({alignment:value})}><Icon size={16}/></button>)}</span></label>
        <label>{t("方向")}<SelectControl compact ariaLabel={t("文字方向")} value={direction || ''} placeholder={multiple ? t("混合") : '—'} disabled={disabled} options={[{value:'vertical',label:t("竖排")},{value:'horizontal',label:t("横排")},{value:'rotated',label:t("旋转")}]} onChange={value => change({orientation:value as TextRegion['orientation']})}/></label>
      </div>
      <div className="inspector-stroke-row">
        <label className="inspector-style-control">{t("样式")}<SelectControl compact ariaLabel={t("排版样式")} value={!regions.length || perspective === undefined ? '' : perspective ? 'perspective' : 'standard'} placeholder={multiple ? t("混合") : '—'} disabled={disabled} options={[{value:'standard',label:t("常规")},{value:'perspective',label:t("随形透视"),disabled:!canWarp}]} onChange={value => {if(value !== 'perspective' || canWarp) change({perspective_warp:value === 'perspective'})}}/></label>
        <label className="inspector-stroke-width">{t("宽度")}<NumberControl commitOnBlur key={regions.map(region => region.id).join(':')} compact ariaLabel={t("描边宽度")} value={common(regions,'stroke_width')} mixed={multiple && common(regions,'stroke_width') === undefined} placeholder={multiple ? t("混合") : '—'} disabled={disabled} min={0} max={20} step={0.5} onChange={value => change({stroke_width:value})}/></label>
        <label className="inspector-color-control">{t("颜色")}<ColorControl compact ariaLabel={t("描边颜色")} value={common(regions,'stroke_color') || '#ffffff'} disabled={disabled} onChange={value => change({stroke_color:value})}/></label>
      </div>
      <div className="inspector-extra-fields">
        <label>{t("行距")}<NumberControl commitOnBlur compact ariaLabel={t("行距")} value={common(regions,'line_spacing')} mixed={multiple && common(regions,'line_spacing') === undefined} placeholder={multiple ? t("混合") : '—'} disabled={disabled} min={0.5} max={3} step={0.05} onChange={value => change({line_spacing:value})}/></label>
        <label>{t("字距")}<NumberControl commitOnBlur compact ariaLabel={t("字距")} value={common(regions,'character_spacing')} mixed={multiple && common(regions,'character_spacing') === undefined} placeholder={multiple ? t("混合") : '—'} disabled={disabled} min={-20} max={100} step={0.5} onChange={value => change({character_spacing:value})}/></label>
      </div>
      <div className="inspector-transform-fields" role="group" aria-label={t("译文位置与旋转")}>
        <label title={t("译文 X 坐标（像素）")}>X<NumberControl commitOnBlur compact ariaLabel={t("译文 X 坐标（像素）")} value={x === undefined ? undefined : coordinateValue(x)} placeholder="—" disabled={transformDisabled} step={1} onChange={value => setPosition('x',value)}/></label>
        <label title={t("译文 Y 坐标（像素）")}>Y<NumberControl commitOnBlur compact ariaLabel={t("译文 Y 坐标（像素）")} value={y === undefined ? undefined : coordinateValue(y)} placeholder="—" disabled={transformDisabled} step={1} onChange={value => setPosition('y',value)}/></label>
        <label title={t("译文 Z 旋转角度（度）")}>Z<NumberControl commitOnBlur compact ariaLabel={t("译文 Z 旋转角度（度）")} value={rotation} mixed={multiple && rotation === undefined} placeholder={multiple ? t("混合") : '—'} disabled={transformDisabled} min={-180} max={180} step={1} onCommit={setRotation} onStep={stepRotation}/></label>
      </div>
    </fieldset>
  </section>
}
