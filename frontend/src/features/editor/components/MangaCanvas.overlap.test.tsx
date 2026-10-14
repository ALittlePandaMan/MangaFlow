import {act, fireEvent, render, screen} from '@testing-library/react'
import type {ReactNode} from 'react'
import {afterAll, beforeAll, beforeEach, describe, expect, it, vi} from 'vitest'
import type {ImagePage, TextRegion, ViewMode} from '../../../types'
import {useEditorStore} from '../store'
import {MangaCanvas} from './MangaCanvas'

vi.mock('konva', () => ({default: {}}))
vi.mock('react-konva', () => {
  const Container = ({children, name}: {children?: ReactNode, name?: string}) => <div data-region={name}>{children}</div>
  const Empty = () => null
  return {Stage: Container, Layer: Container, Group: Container, Circle: Empty, Image: Empty, Line: Empty, Rect: Empty, Text: Empty}
})
vi.mock('../hooks/useImage', () => ({useImage: () => ({image: null, loading: false, error: false})}))
vi.mock('./RegionPolygonEditor', () => ({
  RegionPolygonEditor: ({label, polygon, onCommit}: {label: string, polygon: number[][], onCommit: (polygon: number[][]) => void}) =>
    <button onClick={() => onCommit(polygon.map(([x, y]) => [x + 10, y]))}>{label}</button>,
}))

const page = {id: 'page', width: 800, height: 1000, original_url: '/page.png'} as ImagePage
const region = (id: string, y: number) => ({id, region_key: id, bbox: [20, y, 200, 300],
  polygon: [[20, y], [220, y], [220, y + 300], [20, y + 300]],
  translated_bbox: [40, y, 200, 300], translated_polygon: [[40, y], [240, y], [240, y + 300], [40, y + 300]],
  visible: true, locked: false, rotation: 0, layout_data: {}, translated_text: ''} as TextRegion)
const regions = [region('R010', 20), region('R012', 250), region('R013', 300)]
const props = () => ({page, regions, onCreate: vi.fn(async () => true), onUpdate: vi.fn(async () => undefined),
  onRegionAction: vi.fn(), runningAction: null, interactionKey: 'overlap'})
const order = (container: HTMLElement) => [...container.querySelectorAll('[data-region^="region-"]')]
  .map(node => node.getAttribute('data-region'))

describe('overlapping region interaction order', () => {
  beforeAll(() => vi.stubGlobal('ResizeObserver', class {observe() {} disconnect() {}}))
  afterAll(() => vi.unstubAllGlobals())
  beforeEach(() => useEditorStore.setState({view: 'original', tool: 'select', zoom: 1, selectedIds: ['R010'],
    layers: {original: true, detection: true, clean: false, translated: false}}))

  it('places the selected editor above later overlapping polygons without changing source order', () => {
    const {container} = render(<MangaCanvas {...props()}/>)
    expect(order(container)).toEqual(['region-R012', 'region-R013', 'region-R010'])
    expect(regions.map(region => region.id)).toEqual(['R010', 'R012', 'R013'])
    expect(container.querySelector('[data-region="region-R010"] button')).toHaveTextContent('R010')
    act(() => useEditorStore.getState().select('R012'))
    expect(order(container)).toEqual(['region-R010', 'region-R013', 'region-R012'])
    act(() => useEditorStore.getState().select(null))
    expect(order(container)).toEqual(['region-R010', 'region-R012', 'region-R013'])
  })

  it.each(['original', 'translated'] as ViewMode[])('commits only the selected %s geometry', view => {
    useEditorStore.setState({view})
    const callbacks = props()
    render(<MangaCanvas {...callbacks}/>)
    fireEvent.click(screen.getByRole('button', {name: new RegExp('R010.*框')}))
    const translated = view === 'translated'
    expect(callbacks.onUpdate).toHaveBeenCalledWith('R010', translated
      ? {translated_bbox: [50, 20, 200, 300], translated_polygon: [[50, 20], [250, 20], [250, 320], [50, 320]], rotation: 0}
      : {bbox: [30, 20, 200, 300], polygon: [[30, 20], [230, 20], [230, 320], [30, 320]]})
    expect(callbacks.onUpdate).toHaveBeenCalledTimes(1)
  })

  it('does not enable editing for locked, multi-selected, pan or comparison states', () => {
    const callbacks = props()
    const {container, rerender} = render(<MangaCanvas {...callbacks} regions={regions.map(region => ({...region, locked: true}))}/>)
    expect(order(container)).toEqual(['region-R010', 'region-R012', 'region-R013'])
    expect(screen.queryByRole('button', {name: /R010.*框/})).not.toBeInTheDocument()
    for (const state of [{selectedIds: ['R010', 'R012']}, {selectedIds: ['R010'], tool: 'pan' as const},
      {tool: 'select' as const, view: 'comparison' as const}]) {
      act(() => useEditorStore.setState(state))
      rerender(<MangaCanvas {...callbacks}/>)
      expect(order(container)).toEqual(state.selectedIds?.length === 2 ? ['region-R013','region-R010','region-R012'] : ['region-R010', 'region-R012', 'region-R013'])
      expect(screen.queryByRole('button', {name: /R010.*框/})).not.toBeInTheDocument()
    }
  })
})
