import {describe,expect,it} from 'vitest'
import type {ImagePage} from '../../../types'
import {invalidateHistoryPreview} from './regionHistory'

describe('live region history preview invalidation',()=>{
  it('discards stale rendered text without changing the clean background',()=>{
    const page={id:'page',original_url:'/original.png',clean_url:'/clean.png',rendered_url:'/rendered.png',text_layer_url:'/text.png',text_layer_region_ids:['one']} as ImagePage
    expect(invalidateHistoryPreview(page)).toEqual({...page,rendered_url:null,text_layer_url:null,text_layer_region_ids:null})
  })
  it('does not mutate the authoritative saved page snapshot',()=>{
    const page={id:'page',rendered_url:'/saved.png',text_layer_url:'/saved-text.png',text_layer_region_ids:['one']} as ImagePage
    const result=invalidateHistoryPreview(page)
    expect(result).not.toBe(page)
    expect(page.rendered_url).toBe('/saved.png')
    expect(page.text_layer_url).toBe('/saved-text.png')
    expect(page.text_layer_region_ids).toEqual(['one'])
  })
})
