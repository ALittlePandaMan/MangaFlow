import {t} from '../../../i18n'
import type {ImagePage, TextRegion} from '../../../types'
import {MaskBitmap, maskCoverage, maskRgba} from './maskBitmap'

export interface PreparedMaskScene {
  source: HTMLImageElement
  mask: MaskBitmap
  others: HTMLCanvasElement
}

const loadImage = (url: string) => new Promise<HTMLImageElement>((resolve, reject) => {
  const image = new Image()
  image.crossOrigin = 'anonymous'
  image.onload = () => resolve(image)
  image.onerror = () => reject(new Error(t("无法加载原图或蒙版，请重试")))
  image.src = url
})

function maskSource(region: TextRegion) {
  const url = region.mask_url!
  return /^(data:|blob:)/.test(url) ? url : `${url}${url.includes('?') ? '&' : '?'}v=${encodeURIComponent(region.updated_at)}`
}

/** Prepare offscreen; callers retain the current view until every mask is ready. */
export async function prepareMaskScene(page: ImagePage, region: TextRegion, regions: TextRegion[]): Promise<PreparedMaskScene> {
  // Validate before allocating full-resolution canvases or downloading images.
  let mask = new MaskBitmap(page.width, page.height)
  const [source, selected] = await Promise.all([
    loadImage(page.original_url),
    region.mask_url ? loadImage(maskSource(region)) : Promise.resolve(null),
  ])
  const scratch = document.createElement('canvas')
  scratch.width = page.width; scratch.height = page.height
  const context = scratch.getContext('2d', {willReadFrequently:true})
  const others = document.createElement('canvas')
  others.width = page.width; others.height = page.height
  const otherContext = others.getContext('2d')
  if (!context || !otherContext) throw new Error(t("浏览器不支持画布编辑"))
  const coverage = (image: HTMLImageElement) => {
    if (image.naturalWidth !== page.width || image.naturalHeight !== page.height) throw new Error(t("蒙版尺寸与原图不一致，请重新生成蒙版"))
    context.clearRect(0,0,page.width,page.height)
    context.drawImage(image,0,0)
    return maskCoverage(context.getImageData(0,0,page.width,page.height).data)
  }
  if (selected) mask = new MaskBitmap(page.width,page.height,coverage(selected))
  // One reusable scratch canvas bounds memory even on pages with many regions.
  for (const item of regions) {
    if (item.id === region.id || !item.mask_url) continue
    const pixels = coverage(await loadImage(maskSource(item)))
    context.putImageData(new ImageData(maskRgba(pixels,[117,133,154]),page.width,page.height),0,0)
    otherContext.drawImage(scratch,0,0)
  }
  return {source,mask,others}
}
