import {describe, expect, it} from 'vitest'
import fixtures from './lineBreakFixtures.json'
import {graphemes, wrapParagraph} from './lineBreaking'

describe('shared CJK/Latin line breaking contract', () => {
  it.each(fixtures)('wraps $text at $width cells', fixture => {
    expect(wrapParagraph(fixture.text, items => items.length <= fixture.width)).toEqual(fixture.lines)
  })
  it('keeps combining marks and emoji families together', () => {
    expect(graphemes('á👨‍👩‍👧‍👦')).toEqual(['á', '👨‍👩‍👧‍👦'])
  })
  it('retains upright per-character vertical stacking', () => {
    expect(wrapParagraph('甲play12乙', items => items.length <= 4, false)).toEqual(['甲pla', 'y12乙'])
  })
})
