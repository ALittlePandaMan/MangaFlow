export interface MaskOverlayInput {width:number; height:number; sources:string[]}
export const MASK_OVERLAY_OPACITY=.3
const PIXEL_BUDGET=4_000_000
const MAX_DIMENSION=4096
const COLOR=[57,121,207] as const

/** Read-only, bounded-resolution union of the actual saved mask pixels.
 * Two reusable canvases and one decoded image at a time bound memory. Canvas
 * lighten composes the union; only the final image is read on CPU.
 */
export async function buildMaskOverlay({width,height,sources}:MaskOverlayInput, signal:AbortSignal):Promise<HTMLCanvasElement> {
  signal.throwIfAborted()
  if (![width,height].every(value => Number.isFinite(value) && value>0)) throw new Error('图片尺寸无效')
  const ratio=Math.min(1,Math.sqrt(PIXEL_BUDGET/(width*height)),MAX_DIMENSION/width,MAX_DIMENSION/height)
  const output=document.createElement('canvas'), scratch=document.createElement('canvas')
  output.width=scratch.width=Math.max(1,Math.floor(width*ratio))
  output.height=scratch.height=Math.max(1,Math.floor(height*ratio))
  const context=output.getContext('2d'), sourceContext=scratch.getContext('2d')
  if (!context || !sourceContext) throw new Error('浏览器不支持蒙版预览')
  context.fillStyle='#000';context.fillRect(0,0,output.width,output.height)
  context.globalCompositeOperation='lighten'
  for (const url of sources) {
    signal.throwIfAborted()
    const response=await fetch(url,{signal})
    if (!response.ok) throw new Error('无法读取蒙版')
    const image=await createImageBitmap(await response.blob())
    try {
      signal.throwIfAborted()
      if (image.width !== width || image.height !== height) throw new Error('蒙版尺寸与图片不一致')
      // Flatten alpha over black before taking the maximum. This preserves
      // soft/transparent coverage and prevents overlaps from getting darker.
      sourceContext.fillStyle='#000';sourceContext.fillRect(0,0,scratch.width,scratch.height)
      sourceContext.drawImage(image,0,0,scratch.width,scratch.height)
      context.drawImage(scratch,0,0)
    } finally {image.close()}
  }
  signal.throwIfAborted()
  const pixels=context.getImageData(0,0,output.width,output.height)
  for (let index=0;index<pixels.data.length;index+=4) {
    pixels.data[index+3]=pixels.data[index]
    pixels.data[index]=COLOR[0];pixels.data[index+1]=COLOR[1];pixels.data[index+2]=COLOR[2]
  }
  context.globalCompositeOperation='source-over'
  context.putImageData(pixels,0,0)
  return output
}
