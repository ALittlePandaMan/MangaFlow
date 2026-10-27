// Boundary rules shared with backend/app/services/layout/line_breaking.py.
const opening = new Set(Array.from('([{（［｛〈《「『【〔〖〘〚‘“'))
const closing = new Set(Array.from(')]},.!?:;%）］｝〉》」』】〕〗〙〛、。，．！？：；％’”ぁぃぅぇぉっゃゅょゎァィゥェォッャュョヮー々'))
const segmenter = new Intl.Segmenter('und', {granularity: 'grapheme'})
export const WORD_FIT_REFERENCE_SIZE = 10
export function graphemes(text: string): string[] {
  return Array.from(segmenter.segment(text), entry => entry.segment)
}
function word(character: string) {
  const point = character.codePointAt(0) || 0
  return /^[A-Za-z0-9'_’]/u.test(character) || point >= 0xc0 && point < 0x2e80 && /^[\p{L}\p{N}]/u.test(character)
}
export function wordCount(text: string) {
  let previous = false, count = 0
  for (const item of graphemes(text)) {
    const current = word(item)
    if (current && !previous) count++
    previous = current
  }
  return count
}
export function fittingChunkCount(text: string) {
  let previous = false, count = 0
  for (const item of graphemes(text)) {
    const current = word(item) || closing.has(item) || item === '"'
    if (current && !previous) count++
    previous = current
  }
  return count
}
export function canBreak(left: string, right: string, preserveWords = true) {
  return !(opening.has(left) || closing.has(right) || left === '\u00a0' || right === '\u00a0'
    || preserveWords && (word(left) && word(right) || ['.', ',', ':'].includes(left) && /^[0-9]$/.test(right))
    || left === right && (left === '…' || left === '—'))
}
export function wrapParagraph(text: string, fits: (items: string[]) => boolean, preserveWords = true) {
  const items = graphemes(text)
  if (!items.length) return ['']
  const lines: string[] = []
  let start = 0
  while (start < items.length) {
    let end = start + 1
    while (end <= items.length && fits(items.slice(start, end))) end++
    if (end > items.length) {lines.push(items.slice(start).join('')); break}
    end = Math.max(start + 1, end - 1)
    let boundary = end
    while (boundary > start && boundary < items.length && !canBreak(items[boundary - 1], items[boundary], preserveWords)) boundary--
    if (boundary > start) end = boundary
    lines.push(items.slice(start, end).join('').replace(/ +$/, ''))
    start = end
    while (start < items.length && items[start] === ' ') start++
  }
  return lines
}
