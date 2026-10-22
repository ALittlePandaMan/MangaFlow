import {describe, expect, it} from 'vitest'
import type {TextRegion} from '../../../types'
import {translatedTextBox, translateText} from './textTransform'

const region={bbox:[0,0,100,50],polygon:[[0,0],[100,0],[100,50],[0,50]],translated_bbox:[10,20,80,40],translated_polygon:[[10,20],[90,25],[85,60],[15,55]]} as TextRegion

describe('translated text transforms',() => {
  it('translates a perspective polygon without deforming it or mutating the source',() => {
    const before=JSON.parse(JSON.stringify(region))
    expect(translateText(region,5,-7)).toEqual({
      translated_bbox:[15,13,80,40],translated_polygon:[[15,13],[95,18],[90,53],[20,48]],
    })
    expect(region).toEqual(before)
  })

  it('builds missing translated polygons from their own box rather than the source polygon',() => {
    expect(translateText({...region,translated_polygon:[]},1,2)).toEqual({
      translated_bbox:[11,22,80,40],translated_polygon:[[11,22],[91,22],[91,62],[11,62]],
    })
  })

  it('falls back to source bounds when no translated box exists',() => {
    const legacy={...region,translated_bbox:[],translated_polygon:[]}
    expect(translatedTextBox(legacy)).toEqual(region.bbox)
    expect(translateText(legacy,-5,3)).toEqual({
      translated_bbox:[-5,3,100,50],translated_polygon:[[-5,3],[95,3],[95,53],[-5,53]],
    })
  })
})
