import {AlignLeft, AlignCenter, AlignRight, ChevronDown, Type} from 'lucide-react'
import {useId, useMemo, useState} from 'react'
import type {TextRegion} from '../../../types'
import {ColorControl, NumberControl, SelectControl} from '../../../components/FormControls'
import {cn} from '../../../ui'
import {isPerspectiveQuad} from '../lib/perspectiveText'
import {currentFontSize} from '../lib/inspectorFields'
import {useFontRevision} from '../lib/fontRevision'
import {effectiveTextRotation} from '../lib/textRotation'

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
  const [expanded,setExpanded]=useState(true)
  const fieldsId=useId()
  const disabled=busy || !regions.length, multiple=regions.length>1
  const fontRevision=useFontRevision()
  const sizes=useMemo(() => {void fontRevision; return regions.map(currentFontSize)},[regions,fontRevision])
  const size=sizes.length && sizes.every(value => value === sizes[0]) ? sizes[0] : undefined
  const font=common(regions,'font_family'), weight=common(regions,'font_weight')
  const alignment=common(regions,'alignment'), direction=common(regions,'orientation')
  const perspective=common(regions,'perspective_warp')
  const rotations=regions.map(effectiveTextRotation)
  const rotation=rotations.every(value => value === rotations[0]) ? rotations[0] : undefined
  const canWarp=!!regions.length && regions.every(region => isPerspectiveQuad(region.translated_polygon))
  const change=(patch:Partial<TextRegion>) => {if(!disabled) onChange(regions.map(region => ({id:region.id,patch})))}
  const setSize=(value:number) => change({font_size:Math.max(1,Math.min(512,value)),auto_font_size:false})
  const stepSize=(direction:1|-1) => onChange(regions.map((region,index) => ({id:region.id,patch:{font_size:Math.max(1,Math.min(512,sizes[index]+direction)),auto_font_size:false}})))
  return <section className="inspector-typesetting" aria-label="排版设置">
    <h3 className="inspector-section-heading"><button type="button" className="inspector-section-toggle" aria-expanded={expanded} aria-controls={fieldsId} onClick={() => setExpanded(value => !value)}><Type size={15}/><span>排版</span>{multiple && <small>已选择 {regions.length} 个区域</small>}<ChevronDown size={15} className="inspector-section-chevron"/></button></h3>
    <fieldset id={fieldsId} hidden={!expanded} key={regions.map(region => region.id).join(':')} disabled={disabled} className="inspector-typesetting-fields">
      <div className="inspector-font-row">
        <label>字体<SelectControl compact ariaLabel="字体" value={font || ''} placeholder={multiple ? '多种字体' : '选择文本'} disabled={disabled} options={[...new Set([...fontOptions,...regions.map(region => region.font_family)])].filter(Boolean).map(value => ({value,label:value}))} onChange={value => change({font_family:value})}/></label>
        <label className="inspector-color-control">颜色<ColorControl compact ariaLabel="文字颜色" value={common(regions,'text_color') || '#111111'} disabled={disabled} onChange={value => change({text_color:value})}/></label>
      </div>
      <div className="inspector-size-row">
        <label>字号<NumberControl commitOnBlur key={regions.map(region => region.id).join(':')} compact ariaLabel={multiple ? '批量字号' : '字号'} value={size} mixed={multiple && size === undefined} placeholder={multiple ? '多种字号' : '—'} disabled={disabled} min={1} max={512} step={1} onChange={setSize} onStep={stepSize}/></label>
        <label>字重<SelectControl compact ariaLabel="字重" value={weight === undefined ? '' : String(weight)} placeholder={multiple ? '混合' : '—'} disabled={disabled} options={[100,200,300,400,500,600,700,800,900].map(value => ({value:String(value),label:String(value)}))} onChange={value => change({font_weight:Number(value)})}/></label>
        <label>样式<SelectControl compact ariaLabel="排版样式" value={!regions.length || perspective === undefined ? '' : perspective ? 'perspective' : 'standard'} placeholder={multiple ? '混合' : '—'} disabled={disabled} options={[{value:'standard',label:'常规'},{value:'perspective',label:'随形透视',disabled:!canWarp}]} onChange={value => {if(value !== 'perspective' || canWarp) change({perspective_warp:value === 'perspective'})}}/></label>
      </div>
      <div className="inspector-alignment-row">
        <label>对齐<span className="inspector-alignment-buttons">{([['left','左对齐',AlignLeft],['center','居中',AlignCenter],['right','右对齐',AlignRight]] as const).map(([value,label,Icon]) => <button type="button" key={value} aria-label={`${multiple ? '所选文字' : '文字'}${label}`} aria-pressed={alignment === value} title={label} disabled={disabled} className={cn(alignment === value && 'is-active')} onClick={() => change({alignment:value})}><Icon size={16}/></button>)}</span></label>
        <label>方向<SelectControl compact ariaLabel="文字方向" value={direction || ''} placeholder={multiple ? '混合' : '—'} disabled={disabled} options={[{value:'vertical',label:'竖排'},{value:'horizontal',label:'横排'},{value:'rotated',label:'旋转'}]} onChange={value => change({orientation:value as TextRegion['orientation']})}/></label>
      </div>
      <div className="inspector-stroke-row">
        <label className="inspector-color-control">描边<ColorControl compact ariaLabel="描边颜色" value={common(regions,'stroke_color') || '#ffffff'} disabled={disabled} onChange={value => change({stroke_color:value})}/></label>
        <label className="inspector-stroke-width">宽度<NumberControl commitOnBlur key={regions.map(region => region.id).join(':')} compact ariaLabel="描边宽度" value={common(regions,'stroke_width')} mixed={multiple && common(regions,'stroke_width') === undefined} placeholder={multiple ? '混合' : '—'} disabled={disabled} min={0} max={20} step={0.5} onChange={value => change({stroke_width:value})}/></label>
      </div>
      <div className="inspector-extra-fields">
        <label>行距<NumberControl commitOnBlur compact ariaLabel="行距" value={common(regions,'line_spacing')} mixed={multiple && common(regions,'line_spacing') === undefined} placeholder={multiple ? '混合' : '—'} disabled={disabled} min={0.5} max={3} step={0.05} onChange={value => change({line_spacing:value})}/></label>
        <label>字距<NumberControl commitOnBlur compact ariaLabel="字距" value={common(regions,'character_spacing')} mixed={multiple && common(regions,'character_spacing') === undefined} placeholder={multiple ? '混合' : '—'} disabled={disabled} min={-20} max={100} step={0.5} onChange={value => change({character_spacing:value})}/></label>
        <label>译文旋转<NumberControl commitOnBlur compact ariaLabel="译文旋转角度" value={rotation} mixed={multiple && rotation === undefined} placeholder={multiple ? '混合' : '—'} disabled={disabled} min={-180} max={180} step={1} onCommit={value => change({rotation:value})}/></label>
      </div>
    </fieldset>
  </section>
}
