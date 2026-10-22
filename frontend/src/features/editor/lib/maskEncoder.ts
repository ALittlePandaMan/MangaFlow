import {t} from '../../../i18n'
import {maskRgba, type MaskPatch} from './maskBitmap'

/** PNG work never occupies the painting thread on supported browsers. */
export class MaskEncoder {
  private worker:Worker | null=null
  private unavailable=false
  private sequence=0
  private callbacks=new Map<number,{resolve:(blob:Blob) => void,reject:(error:Error) => void}>()

  encode(patch:MaskPatch):Promise<Blob> {
    if (this.unavailable || typeof Worker === 'undefined' || typeof OffscreenCanvas === 'undefined') return this.fallback(patch)
    if (!this.worker) {
      try {this.worker=new Worker(new URL('./maskEncode.worker.ts',import.meta.url),{type:'module'})}
      catch {this.unavailable=true;return this.fallback(patch)}
      this.worker.onmessage=({data}:{data:{id:number,blob?:Blob,error?:string}}) => {
        const callback=this.callbacks.get(data.id)
        this.callbacks.delete(data.id)
        if (data.blob) callback?.resolve(data.blob)
        else callback?.reject(new Error(t(data.error || '无法导出蒙版')))
      }
      this.worker.onerror=() => {this.unavailable=true;this.dispose()}
    }
    const id=++this.sequence, pixels=patch.pixels.slice()
    return new Promise((resolve,reject) => {
      this.callbacks.set(id,{resolve,reject})
      try {this.worker!.postMessage({id,patch:{...patch,pixels}},[pixels.buffer])}
      catch (error) {this.callbacks.delete(id);reject(error instanceof Error ? error : new Error(String(error)))}
    })
  }

  private fallback(patch:MaskPatch) {
    const canvas=document.createElement('canvas');canvas.width=patch.width;canvas.height=patch.height
    const context=canvas.getContext('2d')
    if (!context) return Promise.reject(new Error(t("无法导出蒙版")))
    context.putImageData(new ImageData(maskRgba(patch.pixels),patch.width,patch.height),0,0)
    return new Promise<Blob>((resolve,reject) => canvas.toBlob(blob => blob ? resolve(blob) : reject(new Error(t("无法导出蒙版"))),'image/png'))
  }
  dispose() {
    this.worker?.terminate();this.worker=null
    for (const callback of this.callbacks.values()) callback.reject(new Error(t("蒙版编码已中断，请重试同步")))
    this.callbacks.clear()
  }
}
