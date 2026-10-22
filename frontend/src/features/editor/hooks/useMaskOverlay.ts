import {t} from '../../../i18n'
import {useEffect,useMemo,useState} from 'react'
import type {ImagePage,TextRegion} from '../../../types'
import {buildMaskOverlay,type MaskOverlayInput} from '../lib/maskOverlay'

type Preview={key:string; image:HTMLCanvasElement|null; error:string|null}

export function useMaskOverlay(page:ImagePage, regions:TextRegion[], enabled:boolean) {
  const key=useMemo(() => {
    if (!enabled) return ''
    // Mask URLs are immutable/versioned by each save. Text, naming, selection
    // and geometry edits must not download all masks again unnecessarily.
    const sources=[...new Set(regions.flatMap(region => region.mask_url ? [region.mask_url] : []))].sort()
    return sources.length ? JSON.stringify({pageId:page.id,width:page.width,height:page.height,sources}) : ''
  },[enabled,page.id,page.width,page.height,regions])
  const [preview,setPreview]=useState<Preview|null>(null)
  useEffect(() => {
    if (!key) {setPreview(null);return}
    const controller=new AbortController()
    void buildMaskOverlay(JSON.parse(key) as MaskOverlayInput,controller.signal).then(image => {
      if (!controller.signal.aborted) setPreview({key,image,error:null})
    }).catch(() => {
      if (!controller.signal.aborted) setPreview({key,image:null,error:t("Mask 叠加加载失败，请关闭后重新开启重试。")})
    })
    return () => controller.abort()
  },[key])
  // Never paint a stale page/mask while its replacement is loading.
  const current=key && preview?.key===key ? preview : null
  return {image:current?.image || null,loading:!!key && !current,error:current?.error || null}
}
