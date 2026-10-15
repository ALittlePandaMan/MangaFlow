import {act,renderHook,waitFor} from '@testing-library/react'
import {beforeEach,describe,expect,it,vi} from 'vitest'
import type {ImagePage,TextRegion} from '../../../types'
import {buildMaskOverlay} from '../lib/maskOverlay'
import {useMaskOverlay} from './useMaskOverlay'

vi.mock('../lib/maskOverlay',() => ({buildMaskOverlay:vi.fn()}))
const page={id:'page',width:100,height:200} as ImagePage
const regions=[{id:'one',mask_url:'/mask-one.png'},{id:'two',mask_url:'/mask-two.png'}] as TextRegion[]

describe('read-only mask overlay loading',() => {
  beforeEach(() => vi.mocked(buildMaskOverlay).mockReset().mockImplementation(async () => document.createElement('canvas')))

  it('does not read or allocate masks until enabled, including pages without saved masks',() => {
    const {result,rerender}=renderHook(({enabled,items}) => useMaskOverlay(page,items,enabled),{initialProps:{enabled:false,items:regions}})
    expect(result.current).toEqual({image:null,loading:false,error:null})
    expect(buildMaskOverlay).not.toHaveBeenCalled()
    rerender({enabled:true,items:[{id:'empty',mask_url:null}] as TextRegion[]})
    expect(buildMaskOverlay).not.toHaveBeenCalled()
    expect(result.current.loading).toBe(false)
  })

  it('keeps one composite across selection, text, naming and unrelated page updates',async () => {
    const {result,rerender}=renderHook(({snapshot,items}) => useMaskOverlay(snapshot,items,true),{initialProps:{snapshot:page,items:regions}})
    await waitFor(() => expect(result.current.image).not.toBeNull())
    const image=result.current.image
    for(let index=0;index<60;index++) rerender({snapshot:{...page},items:regions.map(item => ({...item,region_key:`R${index}`,translated_text:String(index),updated_at:String(index)}))})
    expect(buildMaskOverlay).toHaveBeenCalledOnce()
    expect(result.current.image).toBe(image)
  })

  it('aborts stale loads and never flashes an old mask after changing page or mask URL',async () => {
    let finish!:(image:HTMLCanvasElement) => void
    vi.mocked(buildMaskOverlay).mockImplementationOnce(() => new Promise(resolve => {finish=resolve}))
    const {result,rerender}=renderHook(({snapshot,items}) => useMaskOverlay(snapshot,items,true),{initialProps:{snapshot:page,items:regions}})
    const signal=vi.mocked(buildMaskOverlay).mock.calls[0][1]
    rerender({snapshot:{...page,id:'next'},items:[{...regions[0],mask_url:'/new-mask.png'}]})
    expect(signal.aborted).toBe(true)
    expect(result.current.image).toBeNull()
    await waitFor(() => expect(result.current.image).not.toBeNull())
    const current=result.current.image
    await act(async () => finish(document.createElement('canvas')))
    expect(result.current.image).toBe(current)
    expect(buildMaskOverlay).toHaveBeenLastCalledWith({pageId:'next',width:100,height:200,sources:['/new-mask.png']},expect.any(AbortSignal))
  })

  it('releases its composite and cancels reads when hidden',async () => {
    const {result,rerender}=renderHook(({enabled}) => useMaskOverlay(page,regions,enabled),{initialProps:{enabled:true}})
    await waitFor(() => expect(result.current.image).not.toBeNull())
    const signal=vi.mocked(buildMaskOverlay).mock.calls[0][1]
    rerender({enabled:false})
    expect(signal.aborted).toBe(true)
    expect(result.current).toEqual({image:null,loading:false,error:null})
  })

  it('reports a recoverable loading error without blocking editing',async () => {
    vi.mocked(buildMaskOverlay).mockRejectedValueOnce(new Error('missing mask'))
    const {result,rerender}=renderHook(({enabled}) => useMaskOverlay(page,regions,enabled),{initialProps:{enabled:true}})
    await waitFor(() => expect(result.current.error).toContain('加载失败'))
    expect(result.current.loading).toBe(false)
    rerender({enabled:false});rerender({enabled:true})
    await waitFor(() => expect(result.current.image).not.toBeNull())
    expect(result.current.error).toBeNull()
  })
})
