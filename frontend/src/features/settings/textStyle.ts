import {create} from 'zustand'
import {api} from '../../services/api'
import type {TextStyleDefaults} from '../../types'

export const DEFAULT_TEXT_STYLE: TextStyleDefaults = {
  font_family:'Noto Sans CJK SC',font_size:28,font_weight:400,auto_font_size:true,orientation:'auto',
  text_color:'#111111',stroke_color:'#ffffff',stroke_width:0,alignment:'center',line_spacing:1.15,character_spacing:0,
}

export const useTextStyleStore = create<{defaults: TextStyleDefaults}>(() => ({defaults:{...DEFAULT_TEXT_STYLE}}))
let request: Promise<void> | null = null
let revision = 0

export function setTextStyleDefaults(defaults: TextStyleDefaults) {
  revision++
  useTextStyleStore.setState({defaults:{...DEFAULT_TEXT_STYLE,...defaults}})
}

export function loadTextStyleDefaults(): Promise<void> {
  if (request) return request
  const version = revision
  request = api.preferences.get().then(value => {
    if (version === revision) useTextStyleStore.setState({defaults:{...DEFAULT_TEXT_STYLE,...value.text_style}})
  }).finally(() => {request = null})
  return request
}
