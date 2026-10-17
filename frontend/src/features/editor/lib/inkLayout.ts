/** Ink coordinates relative to an alphabetic baseline, matching Pillow 'ls'. */
export interface GlyphMetrics {
  advance: number
  left: number
  top: number
  right: number
  bottom: number
}
export interface InkGlyph extends GlyphMetrics {
  text: string
  x: number
  y: number
  rotate: boolean
}

export const VERTICAL_FORMS: Record<string, string> = {
  '（': '︵', '）': '︶', '(': '︵', ')': '︶', '【': '︻', '】': '︼',
  '「': '﹁', '」': '﹂', '『': '﹃', '』': '﹄',
  ',': '︐', '，': '︐', '、': '︑', '.': '︒', '．': '︒', '。': '︒',
  ':': '︓', '：': '︓', ';': '︔', '；': '︔', '!': '︕', '！': '︕', '?': '︖', '？': '︖',
  '…': '︙', '—': '︱', 'ー': '︱', '～': '︴', '〜': '︴',
}

export function fitInk(text: string, width: number, height: number, size: number, vertical: boolean,
  lineSpacing: number, characterSpacing: number, alignment: string, measure: (character: string) => GlyphMetrics) {
  const step = Math.max(1, size + characterSpacing), pitch = size * lineSpacing
  type Measured = GlyphMetrics & {text: string, rotate: boolean, w: number, h: number}
  const cache = new Map<string, Measured>()
  function metrics(original: string): Measured {
    const cached = cache.get(original)
    if (cached) return cached
    const character = vertical ? VERTICAL_FORMS[original] || original : original
    const ink = measure(character)
    const rotate = vertical && !VERTICAL_FORMS[original] && original.codePointAt(0)! < 128 && /[\p{L}\p{N}\p{P}]/u.test(original)
    const w = ink.right-ink.left, h = ink.bottom-ink.top
    const result = {...ink, text: character, rotate, w: rotate ? h : w, h: rotate ? w : h}
    cache.set(original, result)
    return result
  }
  const lines: string[] = []
  for (const paragraph of text.replace(/\r/g, '').split('\n')) {
    let current = '', count = 0, cursor = 0, left = Infinity, right = -Infinity
    for (const character of paragraph) {
      const ink = metrics(character)
      let nextLeft = Math.min(left, cursor+ink.left), nextRight = Math.max(right, cursor+ink.right)
      const extent = vertical ? count*step+ink.h : nextRight-nextLeft
      if (count && extent > (vertical ? height : width)) {
        lines.push(current)
        current = ''; count = 0; cursor = 0; nextLeft = ink.left; nextRight = ink.right
      }
      current += character; count += 1; cursor += ink.advance+characterSpacing
      left = nextLeft; right = nextRight
    }
    lines.push(current)
  }
  const glyphs: InkGlyph[] = [], lineWidths: number[] = []
  let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity
  lines.forEach((line, index) => {
    const rowGlyphs: {glyph: InkGlyph, w: number, h: number}[] = []
    let cursor = 0, lineLeft = Infinity, lineRight = -Infinity
    ;[...line].forEach((character, row) => {
      const ink = metrics(character)
      const x = vertical ? -index*pitch+(size-ink.w)/2 : cursor+ink.left
      const y = vertical ? row*step : index*pitch+ink.top
      rowGlyphs.push({glyph: {...ink, x, y}, w: ink.w, h: ink.h})
      lineLeft = Math.min(lineLeft, x); lineRight = Math.max(lineRight, x+ink.w)
      cursor += ink.advance+characterSpacing
    })
    lineWidths.push(rowGlyphs.length ? lineRight-lineLeft : 0)
    if (!rowGlyphs.length) {
      if (vertical) {minX = Math.min(minX, -index*pitch); maxX = Math.max(maxX, -index*pitch+size)}
      else {minY = Math.min(minY, index*pitch-size); maxY = Math.max(maxY, index*pitch)}
      return
    }
    const shift = vertical ? 0 : (alignment === 'left' ? 0 : alignment === 'right' ? width-(lineRight-lineLeft) : (width-(lineRight-lineLeft))/2)-lineLeft
    for (const {glyph, w, h} of rowGlyphs) {
      glyph.x += shift
      minX = Math.min(minX, glyph.x); maxX = Math.max(maxX, glyph.x+w)
      minY = Math.min(minY, glyph.y); maxY = Math.max(maxY, glyph.y+h)
      glyphs.push(glyph)
    }
  })
  const usedWidth = glyphs.length ? maxX-minX : 0, usedHeight = glyphs.length ? maxY-minY : 0
  for (const glyph of glyphs) {glyph.x -= vertical ? minX : 0; glyph.y -= minY}
  return {fontSize: size, lines, lineWidths, glyphs, width: usedWidth, height: usedHeight,
    coordinateWidth: vertical ? usedWidth : width, columnWidth: pitch, cellHeight: step,
    overflow: usedWidth > width || usedHeight > height}
}
