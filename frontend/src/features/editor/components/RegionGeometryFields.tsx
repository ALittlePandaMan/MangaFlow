import {Lock, LockOpen} from 'lucide-react'
import type {TextRegion} from '../../../types'
import {NumberControl} from '../../../components/FormControls'
import {useEditorStore} from '../store'
import {coordinateValue, normalizedAngle, polygonBbox, polygonRotation, rotatePolygon} from '../lib/inspectorFields'

export function RegionGeometryFields({region, onUpdate}: {region:TextRegion; onUpdate:(id:string,patch:Partial<TextRegion>) => void}) {
  const view=useEditorStore(state => state.view)
  const translated=view === 'translated'
  const bbox=translated && region.translated_bbox?.length === 4 ? region.translated_bbox : region.bbox
  const polygon=translated && region.translated_polygon?.length >= 3 ? region.translated_polygon : region.polygon
  const setCoordinate=(axis:'x'|'y',value:number) => {
    if (!Number.isFinite(value)) return
    const dx=axis === 'x' ? value-bbox[0] : 0, dy=axis === 'y' ? value-bbox[1] : 0
    const nextBbox=[bbox[0]+dx,bbox[1]+dy,bbox[2],bbox[3]], nextPolygon=polygon.map(point => [point[0]+dx,point[1]+dy])
    onUpdate(region.id,translated ? {translated_bbox:nextBbox,translated_polygon:nextPolygon} : {bbox:nextBbox,polygon:nextPolygon})
  }
  const setRotation=(value:number) => {
    if (!polygon.length || !Number.isFinite(value)) return
    const delta=normalizedAngle(value-polygonRotation(polygon))
    if (Math.abs(delta)<.001) return
    const next=rotatePolygon(polygon,delta), box=polygonBbox(next)
    onUpdate(region.id,translated ? {translated_polygon:next,translated_bbox:box,rotation:normalizedAngle(region.rotation+delta)} : {polygon:next,bbox:box})
  }
  return <div className="inspector-position-row">
    <details className="inspector-position">
      <summary>位置与旋转 · {translated ? '译文框坐标' : '原文框坐标'}</summary>
      <div className="inspector-coordinate-fields">
        <label>X 坐标<NumberControl compact ariaLabel="X 坐标" value={coordinateValue(bbox[0])} step={1} disabled={region.locked} onChange={value => setCoordinate('x',value)}/></label>
        <label>Y 坐标<NumberControl compact ariaLabel="Y 坐标" value={coordinateValue(bbox[1])} step={1} disabled={region.locked} onChange={value => setCoordinate('y',value)}/></label>
        <label>旋转角度<NumberControl compact ariaLabel={`${translated ? '译文框' : '原文框'}旋转角度（度）`} min={-180} max={180} step={1} disabled={region.locked} value={coordinateValue(polygonRotation(polygon))} onChange={setRotation}/></label>
      </div>
    </details>
    <button type="button" className="inspector-icon-button" aria-label={region.locked ? '解除锁定' : '锁定区域'} title={region.locked ? '解除锁定' : '锁定区域'} onClick={() => onUpdate(region.id,{locked:!region.locked})}>{region.locked ? <Lock size={14}/> : <LockOpen size={14}/>}</button>
  </div>
}
