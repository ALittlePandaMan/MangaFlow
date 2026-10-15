import {beforeEach,describe,expect,it,vi} from 'vitest'
import type {ImagePage,ModelConfiguration,ProcessingTask,TextRegion} from '../../../types'
import {api} from '../../../services/api'
import {createPageWorkflowCommands,type PageWorkflowContext} from './pageWorkflowCommands'

const config={text_threshold:0.25,bubble_threshold:0.5,panel_threshold:0.5,containment_threshold:0.9}
const page={id:'page',status:'DETECTED',text_classification:{version:1,status:'completed',policy:{enabled:true,filter_sfx:true,min_sfx_confidence:0.6}}} as ImagePage
const region={id:'one',locked:false,source_text:'text',layout_data:{detection:{balloon_grouping_version:3,balloon_assignment:{status:'assigned'}}}} as unknown as TextRegion

function context(regions:TextRegion[]):PageWorkflowContext {
  return {getPage:()=>page,getRegions:()=>regions,currentPageId:()=>page.id,isBusy:()=>false,
    confirmDialog:vi.fn(async ()=>false),setView:vi.fn(),setNotice:vi.fn(),setError:vi.fn(),setSchedulingProcess:vi.fn(),
    setRunningWorkflowStage:vi.fn(),setRunningPageTask:vi.fn(),flushPendingRegions:vi.fn(async ()=>{}),
    refreshAfterTask:vi.fn(async ()=>null),syncTaskProgress:vi.fn()}
}

describe('unified layout page migration',()=>{
  beforeEach(()=>{
    vi.restoreAllMocks()
    vi.spyOn(api.models,'list').mockResolvedValue({available:[],configured:[{kind:'detection',provider:'rfdetr',enabled:true,is_default:true,config} as unknown as ModelConfiguration]})
    vi.spyOn(api.images,'process').mockResolvedValue({id:'task',status:'COMPLETED'} as ProcessingTask)
  })

  it('asks before replacing old automatic boxes and leaves them intact when declined',async()=>{
    const ctx=context([region])
    await createPageWorkflowCommands(ctx).processPageStage('ocr')
    expect(ctx.confirmDialog).toHaveBeenCalledWith(expect.objectContaining({title:'使用 RF-DETR 重新检测？',message:expect.stringContaining('手工区域和锁定区域保留')}))
    expect(api.images.process).not.toHaveBeenCalled()
    expect(ctx.flushPendingRegions).not.toHaveBeenCalled()
  })

  it('reruns OCR only on up-to-date RF-DETR frames rather than regenerating their text and geometry',async()=>{
    const current={...region,layout_data:{detection:{detector:'rfdetr',layout_version:2,layout_settings:config,balloon_assignment:{status:'unassigned'}}}}
    const ctx=context([current])
    await createPageWorkflowCommands(ctx).processPageStage('ocr')
    expect(ctx.confirmDialog).not.toHaveBeenCalled()
    expect(api.images.process).toHaveBeenCalledWith(page.id,expect.objectContaining({start_stage:'ocr',end_stage:'ocr'}))
  })

  it('preserves old locked frames while migrating the unlocked frames',async()=>{
    const locked={...region,id:'locked',locked:true,layout_data:{detection:{balloon_grouping_version:2,balloon_assignment:{status:'unavailable'}}}}
    const ctx=context([locked,region])
    vi.mocked(ctx.confirmDialog).mockResolvedValue(true)
    vi.mocked(ctx.refreshAfterTask).mockResolvedValue({task:{status:'COMPLETED'} as ProcessingTask,regions:[locked]})
    await createPageWorkflowCommands(ctx).processPageStage('ocr')
    expect(ctx.confirmDialog).toHaveBeenCalledWith(expect.objectContaining({title:'使用 RF-DETR 重新检测？'}))
    expect(api.images.process).toHaveBeenCalledWith(page.id,expect.objectContaining({start_stage:'detection',end_stage:'ocr'}))
    expect(ctx.setError).not.toHaveBeenCalledWith(expect.stringMatching(/解锁|气泡合并未执行/))
    expect(locked.locked).toBe(true)
  })

  it('allows OCR-only reruns on a page containing only old locked frames',async()=>{
    const ctx=context([{...region,locked:true,layout_data:{detection:{balloon_grouping_version:2}}}])
    await createPageWorkflowCommands(ctx).processPageStage('ocr')
    expect(ctx.confirmDialog).not.toHaveBeenCalled()
    expect(api.images.process).toHaveBeenCalledWith(page.id,expect.objectContaining({start_stage:'ocr'}))
  })

  it('offers migration from RF-DETR v1 without changing the page when declined',async()=>{
    const ctx=context([{...region,layout_data:{detection:{detector:'rfdetr',layout_version:1,layout_settings:config}}}])
    await createPageWorkflowCommands(ctx).processPageStage('ocr')
    expect(ctx.confirmDialog).toHaveBeenCalledOnce()
    expect(api.images.process).not.toHaveBeenCalled()
  })
})
