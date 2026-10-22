import {t} from '../../../i18n'
import {api} from '../../../services/api'
import {maskCoverage,type MaskCheckpoint,type MaskBounds} from './maskBitmap'

async function pixels(url:string,bounds:MaskBounds) {
  const image=await new Promise<HTMLImageElement>((resolve,reject) => {
    const value=new Image();value.crossOrigin='anonymous';value.onload=() => resolve(value)
    value.onerror=() => reject(new Error(t("无法读取蒙版历史")));value.src=url
  })
  if (image.naturalWidth !== bounds.width || image.naturalHeight !== bounds.height) throw new Error(t("蒙版历史尺寸不匹配"))
  const canvas=document.createElement('canvas');canvas.width=bounds.width;canvas.height=bounds.height
  const context=canvas.getContext('2d',{willReadFrequently:true})
  if (!context) throw new Error(t("无法读取蒙版历史"))
  context.drawImage(image,0,0)
  return {...bounds,pixels:maskCoverage(context.getImageData(0,0,bounds.width,bounds.height).data)}
}

/** Warm sparse checkpoints in the background, including after a page refresh. */
export async function loadMaskHistory(id:string,width:number,height:number) {
  const result=await api.regions.maskHistory(id)
  let budget=64_000_000
  const take=(steps:typeof result.undo) => {
    const selected=[]
    for (const step of steps) {
      if (![step.x,step.y,step.width,step.height].every(Number.isInteger) || step.x < 0 || step.y < 0 || step.width < 1 || step.height < 1 || step.x+step.width > width || step.y+step.height > height) break
      const bytes=step.width*step.height*2
      if (bytes > budget) break
      budget-=bytes;selected.push(step)
    }
    return selected
  }
  const undo=take(result.undo),redo=take(result.redo)
  const decode=async (step:typeof result.undo[number]):Promise<MaskCheckpoint> => {
    const bounds={x:step.x,y:step.y,width:step.width,height:step.height}
    const [before,after]=await Promise.all([pixels(step.before_url,bounds),pixels(step.after_url,bounds)])
    return {before,after}
  }
  const loaded:MaskCheckpoint[]=[]
  const all=[...undo,...redo]
  // Bound simultaneous image decodes; patches are much smaller than a page.
  for (let index=0;index<all.length;index+=4) loaded.push(...await Promise.all(all.slice(index,index+4).map(decode)))
  return {...result,undo:loaded.slice(0,undo.length),redo:loaded.slice(undo.length)}
}
