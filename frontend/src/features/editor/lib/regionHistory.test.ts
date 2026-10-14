import {describe,expect,it} from 'vitest'
import type {TextRegion} from '../../../types'
import {previewRegionHistory,type RegionHistoryPreview} from './regionHistory'

const region=(id:string,order:number,x:number)=>({id,reading_order:order,bbox:[x,10,20,30]} as TextRegion)
describe('local region history previews',()=>{
  const first=region('one',1,10),second=region('two',2,50),third=region('three',3,90)
  it('restores only affected boxes and preserves unrelated newer edits',()=>{
    const step:RegionHistoryPreview={pageId:'page',ids:['one','two'],before:[first,second],after:[region('one',1,25),region('two',2,65)],sequence:1}
    const current=[...step.after,{...third,source_text:'new'}]
    const undo=previewRegionHistory(current,step,'undo')
    expect(undo).toEqual([first,second,current[2]])
    expect(previewRegionHistory(undo,step,'redo')).toEqual(current)
  })
  it('removes a just-created box on undo and restores the same ID on redo',()=>{
    const step:RegionHistoryPreview={pageId:'page',ids:['three'],before:[],after:[third],sequence:1}
    expect(previewRegionHistory([first,second,third],step,'undo')).toEqual([first,second])
    expect(previewRegionHistory([first,second],step,'redo')).toEqual([first,second,third])
  })
})
