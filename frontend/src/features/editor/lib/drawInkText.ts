import type {TextRegion} from '../../../types'
import type {TextSizing} from './fontSizing'

/** sceneFunc ink can extend beyond Shape.width/height, unlike Konva.Text. */
export function inkTextBounds(layout: TextSizing, width: number, height: number, strokeWidth: number, rotation: number) {
  const offsetX = (width-layout.coordinateWidth)/2, offsetY = (height-layout.height)/2
  const margin = Math.max(2, strokeWidth+2)
  let left = 0, top = 0, right = width, bottom = height
  for (const glyph of layout.glyphs ?? []) {
    const w = glyph.rotate ? glyph.bottom-glyph.top : glyph.right-glyph.left
    const h = glyph.rotate ? glyph.right-glyph.left : glyph.bottom-glyph.top
    left = Math.min(left, offsetX+glyph.x-margin)
    top = Math.min(top, offsetY+glyph.y-margin)
    right = Math.max(right, offsetX+glyph.x+w+margin)
    bottom = Math.max(bottom, offsetY+glyph.y+h+margin)
  }
  const angle = rotation*Math.PI/180, cosine = Math.cos(angle), sine = Math.sin(angle)
  const corners = [[left,top],[right,top],[right,bottom],[left,bottom]].map(([x,y]) => {
    const dx = x-width/2, dy = y-height/2
    return [width/2+dx*cosine-dy*sine, height/2+dx*sine+dy*cosine]
  })
  const x = Math.floor(Math.min(...corners.map(point => point[0])))
  const y = Math.floor(Math.min(...corners.map(point => point[1])))
  return {x,y,width:Math.ceil(Math.max(...corners.map(point => point[0])))-x,
    height:Math.ceil(Math.max(...corners.map(point => point[1])))-y}
}

/** Draw the same ink corners used by fitting, without Konva's em-box offsets. */
export function drawInkText(context: CanvasRenderingContext2D, region: TextRegion, layout: TextSizing,
  style: {textColor: string, strokeColor: string, strokeWidth: number}, width: number, height: number) {
  context.save()
  context.font = `${region.font_weight >= 600 ? 'bold' : 'normal'} ${layout.fontSize}px "${region.font_family}"`
  context.textBaseline = 'alphabetic'
  context.textAlign = 'left'
  context.fillStyle = style.textColor
  context.strokeStyle = style.strokeColor
  // Pillow's stroke expands by this radius; Canvas lineWidth spans both sides.
  context.lineWidth = style.strokeWidth*2
  context.lineJoin = 'round'
  const offsetX = (width-layout.coordinateWidth)/2, offsetY = (height-layout.height)/2
  for (const glyph of layout.glyphs ?? []) {
    context.save()
    context.translate(offsetX+glyph.x, offsetY+glyph.y)
    if (glyph.rotate) {
      context.translate(glyph.bottom-glyph.top, 0)
      context.rotate(Math.PI/2)
    }
    if (style.strokeWidth > 0) context.strokeText(glyph.text, -glyph.left, -glyph.top)
    context.fillText(glyph.text, -glyph.left, -glyph.top)
    context.restore()
  }
  context.restore()
}
