import {maskRgba, type MaskPatch} from './maskBitmap'

const scope=self as unknown as {onmessage:(event:MessageEvent<{id:number,patch:MaskPatch}>) => void,postMessage:(value:unknown) => void}
scope.onmessage=async ({data:{id,patch}}) => {
  try {
    const canvas=new OffscreenCanvas(patch.width,patch.height)
    const context=canvas.getContext('2d')
    if (!context) throw new Error('无法导出蒙版')
    context.putImageData(new ImageData(maskRgba(patch.pixels),patch.width,patch.height),0,0)
    scope.postMessage({id,blob:await canvas.convertToBlob({type:'image/png'})})
  } catch (error) {scope.postMessage({id,error:error instanceof Error ? error.message : String(error)})}
}
