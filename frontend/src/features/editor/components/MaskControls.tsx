import {t, useLocale} from '../../../i18n'
import {BoxSelect, Eraser, Hand, LassoSelect, Layers3, MousePointer2, Paintbrush, PenTool} from 'lucide-react'
import type {CSSProperties} from 'react'
import {SelectControl} from '../../../components/FormControls'
import type {TextRegion, Tool} from '../../../types'
import {cn} from '../../../ui'
import type {MaskTool} from '../lib/maskBitmap'

const toolDetails = {
  brush: {get label(){return t("画笔")}, get detail(){return t("涂抹添加修复范围")}, Icon: Paintbrush},
  eraser: {get label(){return t("橡皮擦")}, get detail(){return t("擦除多余蒙版，保留原图")}, Icon: Eraser},
  select: {get label(){return t("选择区域")}, get detail(){return t("点击画布切换编辑区域")}, Icon: MousePointer2},
  pan: {get label(){return t("移动画布")}, get detail(){return t("拖拽移动，滚轮缩放")}, Icon: Hand},
  rectangle: {get label(){return t("矩形")}, get detail(){return t("拖拽框选，补充蒙版")}, Icon: BoxSelect},
  polygon: {get label(){return t("多边形")}, get detail(){return t("逐点绘制，靠近首点高亮后点击闭合")}, Icon: PenTool},
  lasso: {get label(){return t("套索")}, get detail(){return t("自由圈选，松开完成")}, Icon: LassoSelect},
} satisfies Record<Tool, {label: string, detail: string, Icon: typeof Paintbrush}>

function MaskRange({label, ariaLabel, value, min, max, step = 1, unit, disabled, onChange}: {
  label: string, ariaLabel: string, value: number, min: number, max: number, step?: number, unit: string, disabled: boolean, onChange: (value: number) => void
}) {
  useLocale()
  return <label className="mask-range-field">
    <span className="mask-field-heading"><span>{label}</span><output className="mask-value">{value}<span>{unit}</span></output></span>
    <input className="mask-range motion-range" type="range" aria-label={ariaLabel} min={min} max={max} step={step} value={value} disabled={disabled} style={{'--range-progress': `${(value-min)/(max-min)*100}%`} as CSSProperties} onChange={event => onChange(Number(event.target.value))}/>
  </label>
}

/** One property panel, portaled into the workbench's existing responsive sheet. */
export function MaskControls({region, regions, tool, operation, diameter, referenceOpacity, showOriginal, disabled, onRegionChange, onToolChange, onDiameterChange, onReferenceOpacityChange, onReferenceChange}: {
  region: TextRegion, regions: TextRegion[], tool: Tool, operation: MaskTool, diameter: number, referenceOpacity: number, showOriginal: boolean, disabled: boolean,
  onRegionChange: (id: string) => void, onToolChange: (tool: Tool) => void, onDiameterChange: (value: number) => void, onReferenceOpacityChange: (value: number) => void, onReferenceChange: (value: boolean) => void
}) {
  useLocale()
  const brush = tool === 'brush' || tool === 'eraser'
  const drawing = brush || tool === 'rectangle' || tool === 'polygon' || tool === 'lasso'
  const {label, Icon} = toolDetails[drawing ? operation : tool]
  const detail = drawing && !brush ? `${toolDetails[tool].label}${operation === 'eraser' ? t("圈选擦除蒙版") : t("圈选添加修复范围")}` : toolDetails[drawing ? operation : tool].detail
  return <div className="mask-inspector" aria-label={t("蒙版显示设置")}>
    <header className="mask-inspector-heading"><Layers3 size={16} aria-hidden="true"/><h2>{t("蒙版设置")}</h2></header>
    <div className="mask-inspector-body app-scrollbars">
      <div className="mask-control-section">
        <div className="mask-field-heading"><span>{t("编辑区域")}</span><span className="mask-caption">{t("共")}{' '}{regions.length} {' '}{t("个")}</span></div>
        <SelectControl ariaLabel={t("当前蒙版区域")} value={region.id} disabled={disabled} onChange={onRegionChange} options={regions.map(item => ({value:item.id, label:`${item.region_key}${item.locked ? t(" · 已锁定") : ''}`}))}/>
        <div className="mask-legend" aria-label={t("蒙版颜色说明")}><span><i className="mask-current-dot"/>{t("当前区域")}</span><span><i className="mask-other-dot"/>{t("其他区域")}</span></div>
      </div>

      <div className="mask-control-section">
        <div className="mask-tool-summary"><span className="mask-tool-icon"><Icon size={19} aria-hidden="true"/></span><div><strong>{label}</strong><p>{detail}</p></div></div>
        {brush && <><MaskRange label={tool === 'eraser' ? t("橡皮擦大小") : t("笔刷大小")} ariaLabel={t("蒙版笔刷大小")} value={diameter} min={2} max={160} unit="px" disabled={disabled || region.locked} onChange={onDiameterChange}/>
          <div className="mask-brush-presets" aria-label={t("笔刷尺寸预设")}>{[{value:8,label:t("精细")},{value:24,label:t("标准")},{value:64,label:t("大面积")}].map(item => <button key={item.value} type="button" disabled={disabled || region.locked} aria-pressed={diameter === item.value} onClick={() => onDiameterChange(item.value)}><i style={{width:item.value === 8 ? 4 : item.value === 24 ? 8 : 12,height:item.value === 8 ? 4 : item.value === 24 ? 8 : 12}}/>{item.label}</button>)}</div></>}
        <div className="mask-field-heading">{t("画笔形状")}</div>
        <div className="mask-brush-presets mask-shape-presets" aria-label={t("蒙版形状工具")}>
          <button type="button" disabled={disabled || region.locked} aria-label={t("自由画笔")} aria-pressed={brush} onClick={() => onToolChange(operation)}><Paintbrush size={14} aria-hidden="true"/>{t("自由画笔")}</button>
          {(['rectangle','polygon','lasso'] as const).map(id => {
          const shape = toolDetails[id]
          return <button key={id} type="button" disabled={disabled || region.locked} aria-label={t("{{v0}}{{v1}}蒙版", {v0: shape.label, v1: operation === 'eraser' ? t("擦除") : t("补画")})} aria-pressed={tool === id} onClick={() => onToolChange(id)}><shape.Icon size={14} aria-hidden="true"/>{shape.label}</button>
        })}</div>
      </div>

      <div className="mask-control-section">
        <h3 className="mask-section-label">{t("显示")}</h3>
        <label className={cn('mask-reference', disabled && 'is-disabled')}>
          <span><strong>{t("原图参考")}</strong><small>{t("叠加原图，检查修复边界")}</small></span>
          <input type="checkbox" aria-label={t("原图参考")} checked={showOriginal} disabled={disabled} onChange={event => onReferenceChange(event.target.checked)}/>
          <span className="mask-switch" aria-hidden="true"/>
        </label>
        <MaskRange label={t("原图不透明度")} ariaLabel={t("原图参考不透明度")} value={referenceOpacity} min={0} max={100} step={5} unit="%" disabled={disabled || !showOriginal} onChange={onReferenceOpacityChange}/>
      </div>

    </div>
  </div>
}
