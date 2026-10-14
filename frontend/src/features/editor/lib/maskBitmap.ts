export type MaskPoint = {x: number, y: number}
export type MaskTool = 'brush' | 'eraser'
export type MaskBounds = {x: number, y: number, width: number, height: number}
export type MaskPatch = MaskBounds & {pixels: Uint8Array}
export type MaskCheckpoint = {before: MaskPatch, after: MaskPatch}

/** Full-resolution coverage, independent of the overlay's display opacity. */
export class MaskBitmap {
  pixels: Uint8Array
  private before: Uint8Array | null = null
  private undoStack: MaskCheckpoint[] = []
  private redoStack: MaskCheckpoint[] = []
  private strokeBounds: MaskBounds | null = null
  private lastBounds: MaskBounds | null = null
  private changedPixels = 0
  private readonly baseline: Uint8Array

  constructor(readonly width: number, readonly height: number, pixels?: Uint8Array) {
    if (!Number.isInteger(width) || !Number.isInteger(height) || width <= 0 || height <= 0 || width * height > 32_000_000) {
      throw new Error('图片尺寸不支持蒙版编辑（最大 3200 万像素）')
    }
    if (pixels && pixels.length !== width * height) throw new Error('蒙版尺寸与原图不一致')
    this.pixels = pixels ? pixels.slice() : new Uint8Array(width * height)
    this.baseline = this.pixels.slice()
  }

  get canUndo() { return this.undoStack.length > 0 }
  get canRedo() { return this.redoStack.length > 0 }
  get hasHistory() {return this.canUndo || this.canRedo}
  get dirty() { return this.changedPixels > 0 }

  begin() { if (!this.before) this.before = this.pixels.slice() }

  markSaved(pixels = this.pixels) {
    this.baseline.set(pixels)
    this.changedPixels = 0
    for (let i=0;i<pixels.length;i++) if (this.pixels[i] !== pixels[i]) this.changedPixels++
  }

  markPatchSaved(patch: MaskPatch) {
    for (let y=0;y<patch.height;y++) for (let x=0;x<patch.width;x++) {
      const index=(patch.y+y)*this.width+patch.x+x, saved=patch.pixels[y*patch.width+x]
      this.changedPixels += Number(this.pixels[index] !== saved)-Number(this.pixels[index] !== this.baseline[index])
      this.baseline[index]=saved
    }
  }

  /** A finished stroke uploads only its dirty rectangle, not the full page. */
  patch(): MaskPatch {
    const bounds=this.lastBounds || {x:0,y:0,width:this.width,height:this.height}
    return this.crop(this.pixels,bounds)
  }

  private crop(source:Uint8Array,bounds:MaskBounds):MaskPatch {
    const pixels=new Uint8Array(bounds.width*bounds.height)
    for (let y=0;y<bounds.height;y++) {
      const index=(bounds.y+y)*this.width+bounds.x
      pixels.set(source.subarray(index,index+bounds.width),y*bounds.width)
    }
    return {...bounds,pixels}
  }

  private write(x: number, y: number, value: number) {
    const index=y*this.width+x, old=this.pixels[index]
    if (old === value) return
    this.begin()
    this.changedPixels += Number(value !== this.baseline[index])-Number(old !== this.baseline[index])
    this.pixels[index]=value
    const box=this.strokeBounds
    if (!box) this.strokeBounds={x,y,width:1,height:1}
    else {
      const right=Math.max(box.x+box.width,x+1),bottom=Math.max(box.y+box.height,y+1)
      box.x=Math.min(box.x,x);box.y=Math.min(box.y,y);box.width=right-box.x;box.height=bottom-box.y
    }
  }

  fillPolygon(points: MaskPoint[]) {
    if (points.length < 3 || points.some(point => !Number.isFinite(point.x) || !Number.isFinite(point.y))) return
    // Scanline fill: keep polygon/rectangle/lasso additions in original pixels.
    const top = Math.max(0, Math.floor(Math.min(...points.map(point => point.y))))
    const bottom = Math.min(this.height - 1, Math.ceil(Math.max(...points.map(point => point.y))))
    for (let y = top; y <= bottom; y++) {
      const crossings: number[] = []
      for (let i = 0, j = points.length - 1; i < points.length; j = i++) {
        const a = points[i], b = points[j], scan = y + .5
        if ((a.y > scan) !== (b.y > scan)) crossings.push(a.x + (scan - a.y) * (b.x - a.x) / (b.y - a.y))
      }
      crossings.sort((a,b) => a-b)
      for (let i = 0; i + 1 < crossings.length; i += 2) {
        const left = Math.min(this.width, Math.max(0, Math.ceil(crossings[i] - .5))), right = Math.max(0, Math.min(this.width, Math.ceil(crossings[i+1] - .5)))
        for (let x=left;x<right;x++) this.write(x,y,255)
      }
    }
  }

  paint(from: MaskPoint, to: MaskPoint, diameter: number, tool: MaskTool) {
    if (![from.x, from.y, to.x, to.y, diameter].every(Number.isFinite) || diameter <= 0) return
    const radius = diameter / 2
    const x0 = Math.max(0, Math.floor(Math.min(from.x, to.x) - radius))
    const y0 = Math.max(0, Math.floor(Math.min(from.y, to.y) - radius))
    const x1 = Math.min(this.width - 1, Math.ceil(Math.max(from.x, to.x) + radius))
    const y1 = Math.min(this.height - 1, Math.ceil(Math.max(from.y, to.y) + radius))
    const dx = to.x - from.x, dy = to.y - from.y, length = dx * dx + dy * dy
    for (let y = y0; y <= y1; y++) for (let x = x0; x <= x1; x++) {
      const ratio = length ? Math.max(0, Math.min(1, ((x + .5 - from.x) * dx + (y + .5 - from.y) * dy) / length)) : 0
      if ((x + .5 - from.x - ratio * dx) ** 2 + (y + .5 - from.y - ratio * dy) ** 2 <= radius * radius) {
        this.write(x,y,tool === 'eraser' ? 0 : 255)
      }
    }
  }

  commit() {
    const before = this.before
    const bounds = this.strokeBounds
    this.before = null
    this.strokeBounds = null
    if (!before || !bounds) return false
    let changed=false
    for (let y=bounds.y;y<bounds.y+bounds.height && !changed;y++) for (let x=bounds.x;x<bounds.x+bounds.width;x++) {
      const index=y*this.width+x
      if (this.pixels[index] !== before[index]) {changed=true;break}
    }
    if (!changed) return false
    this.lastBounds=bounds
    this.undoStack.push({before:this.crop(before,bounds),after:this.crop(this.pixels,bounds)})
    // Sparse checkpoints retain up to 100 strokes even on a 32MP page, within
    // a fixed memory budget. Large whole-page edits keep at least one step.
    let bytes=this.undoStack.reduce((total,item) => total+item.before.pixels.byteLength+item.after.pixels.byteLength,0)
    while (this.undoStack.length > 1 && (this.undoStack.length > 100 || bytes > 64_000_000)) {
      const oldest=this.undoStack.shift()!
      bytes-=oldest.before.pixels.byteLength+oldest.after.pixels.byteLength
    }
    this.redoStack = []
    return true
  }

  private restore(pixels: Uint8Array, bounds: MaskBounds) {
    for (let y=bounds.y;y<bounds.y+bounds.height;y++) for (let x=bounds.x;x<bounds.x+bounds.width;x++) {
      const index=y*this.width+x
      this.changedPixels += Number(pixels[index] !== this.baseline[index])-Number(this.pixels[index] !== this.baseline[index])
    }
    for (let y=bounds.y;y<bounds.y+bounds.height;y++) {
      const index=y*this.width+bounds.x
      this.pixels.set(pixels.subarray(index,index+bounds.width),index)
    }
    this.lastBounds=bounds
  }
  cancel() {
    if (this.before && this.strokeBounds) this.restore(this.before,this.strokeBounds)
    this.before=null;this.strokeBounds=null
  }
  applyPatch(patch:MaskPatch) {
    for (let y=0;y<patch.height;y++) for (let x=0;x<patch.width;x++) {
      const index=(patch.y+y)*this.width+patch.x+x,value=patch.pixels[y*patch.width+x]
      this.changedPixels+=Number(value !== this.baseline[index])-Number(this.pixels[index] !== this.baseline[index])
      this.pixels[index]=value
    }
    this.lastBounds=patch
  }
  seedHistory(undo:MaskCheckpoint[],redo:MaskCheckpoint[]) {
    if (this.before || this.hasHistory) return false
    this.undoStack=[...undo].reverse();this.redoStack=[...redo].reverse()
    return true
  }
  undo() { const previous=this.undoStack.pop(); if (previous) {this.redoStack.push(previous);this.applyPatch(previous.before)} }
  redo() { const next=this.redoStack.pop(); if (next) {this.undoStack.push(next);this.applyPatch(next.after)} }
}

export function maskCoverage(rgba: Uint8ClampedArray) {
  const pixels = new Uint8Array(rgba.length / 4)
  for (let i = 0; i < pixels.length; i++) pixels[i] = Math.round(rgba[i * 4] * rgba[i * 4 + 3] / 255)
  return pixels
}

export function maskRgba(pixels: Uint8Array, color: readonly number[] = [255, 255, 255]) {
  const rgba = new Uint8ClampedArray(pixels.length * 4)
  for (let i = 0; i < pixels.length; i++) {
    rgba[i * 4] = color[0]; rgba[i * 4 + 1] = color[1]; rgba[i * 4 + 2] = color[2]
    rgba[i * 4 + 3] = pixels[i]
  }
  return rgba
}
