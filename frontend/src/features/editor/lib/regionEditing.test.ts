import {describe,expect,it} from 'vitest'
import type {TextRegion} from '../../../types'
import {alignRegions,constrainGroupMove,draftRegion,moveRegions} from './regionEditing'

const region = (id:string,x:number,y:number,locked=false) => ({id,locked,region_key:id,reading_order:1,bbox:[x,y,30,40],polygon:[[x,y],[x+30,y],[x+30,y+40],[x,y+40]],
  translated_bbox:[x+10,y+10,20,30],translated_polygon:[[x+10,y+10],[x+30,y+10],[x+30,y+40],[x+10,y+40]]} as TextRegion)

describe('group editing geometry',() => {
  const regions = [region('R001',20,20),region('R002',60,70),region('R003',10,10,true)]
  it.each([false,true])('moves only the active frame and keeps spacing and locked boxes (%s)',translated => {
    const changes=moveRegions(regions,translated,12,-6)
    expect(changes).toHaveLength(2)
    expect(changes[0].patch).toEqual(translated ? {translated_bbox:[42,24,20,30],translated_polygon:[[42,24],[62,24],[62,54],[42,54]]}
      : {bbox:[32,14,30,40],polygon:[[32,14],[62,14],[62,54],[32,54]]})
    expect(regions[0].bbox).toEqual([20,20,30,40])
  })
  it('clamps a single shared delta at every page edge',() => {
    expect(constrainGroupMove(regions,false,100,100,120,140)).toEqual({x:30,y:30})
    expect(constrainGroupMove(regions,false,-100,-100,120,140)).toEqual({x:-20,y:-20})
    expect(constrainGroupMove(regions.map(item=>({...item,locked:true})),false,10,10,120,140)).toEqual({x:0,y:0})
  })
  it.each(['left','center','right','top','middle','bottom'] as const)('aligns %s within the selected bounds without changing shape',alignment => {
    const changes=alignRegions(regions,false,alignment)
    expect(changes.every(change=>change.id !== 'R003')).toBe(true)
    const after=regions.slice(0,2).map(item=>({...item,...changes.find(change=>change.id===item.id)?.patch}))
    const coordinate = (box:number[]) => alignment==='left' ? box[0] : alignment==='right' ? box[0]+box[2] : alignment==='center' ? box[0]+box[2]/2
      : alignment==='top' ? box[1] : alignment==='bottom' ? box[1]+box[3] : box[1]+box[3]/2
    expect(coordinate(after[0].bbox)).toBe(coordinate(after[1].bbox))
    expect(after.map(item=>item.bbox.slice(2))).toEqual([[30,40],[30,40]])
  })
  it('creates a stable UUID with independent source and translated points',() => {
    const draft=draftRegion('page',[[10,10],[50,10],[30,80]],[10,10,40,70],regions)
    expect(draft.id).toMatch(/^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/)
    expect(draft.translated_polygon).toEqual(draft.polygon)
    expect(draft.translated_polygon).not.toBe(draft.polygon)
    expect(draft.translated_polygon[0]).not.toBe(draft.polygon[0])
    expect(draft.orientation).toBe('vertical')
  })
})
