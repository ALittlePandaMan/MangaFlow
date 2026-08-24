import {act, render, screen} from '@testing-library/react'
import type {ReactNode} from 'react'
import {afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi} from 'vitest'

import type {ImagePage} from '../../../types'
import {useEditorStore} from '../store'
import {MangaCanvas} from './MangaCanvas'

type MockNodeProps = {children?: ReactNode}

vi.mock('konva', () => {
  class Group {
    add() {}
    destroy() {}
    toCanvas() { return document.createElement('canvas') }
  }

  class Text {}

  return {default: {Group, Text}}
})

vi.mock('react-konva', () => {
  const Container = ({children}: MockNodeProps) => <>{children}</>
  const EmptyNode = () => null

  return {
    Stage: ({children}: MockNodeProps) => <div data-testid="konva-stage">{children}</div>,
    Layer: Container,
    Group: Container,
    Circle: EmptyNode,
    Image: EmptyNode,
    Line: EmptyNode,
    Rect: EmptyNode,
    Text: EmptyNode,
  }
})

vi.mock('../hooks/useImage', () => ({
  useImage: vi.fn(() => ({image: null, loading: true, error: false})),
  versionedImageSource: (source: string) => source,
}))

const page: ImagePage = {
  id: 'image-1',
  project_id: 'project-1',
  filename: 'page.png',
  width: 1200,
  height: 1800,
  order_index: 0,
  status: 'UPLOADED',
  current_stage: null,
  error_message: null,
  ocr_exempt: false,
  original_url: '/media/page.png',
  clean_url: '/media/page-clean.png',
  rendered_url: null,
  text_layer_url: null,
  created_at: '2026-08-24T00:00:00Z',
  updated_at: '2026-08-24T00:00:00Z',
}

class ResizeObserverMock {
  observe() {}
  disconnect() {}
}

describe('MangaCanvas image loading', () => {
  beforeAll(() => vi.stubGlobal('ResizeObserver', ResizeObserverMock))
  afterAll(() => vi.unstubAllGlobals())

  beforeEach(() => {
    vi.useFakeTimers()
    useEditorStore.setState({
      view: 'translated',
      tool: 'select',
      zoom: 1,
      selectedIds: [],
      layers: {original: false, detection: true, clean: true, translated: true},
    })
  })
  afterEach(() => vi.useRealTimers())

  it('keeps the canvas visible without showing an image-loading overlay', () => {
    render(<MangaCanvas
      page={page}
      regions={[]}
      onCreate={vi.fn(async () => true)}
      onUpdate={vi.fn(async () => undefined)}
      onRegionAction={vi.fn()}
      runningAction={null}
    />)

    act(() => vi.advanceTimersByTime(250))

    expect(screen.getByTestId('konva-stage')).toBeInTheDocument()
    expect(screen.queryByText('正在载入画布图片')).not.toBeInTheDocument()
    expect(screen.queryByRole('status', {name: '正在载入画布图片'})).not.toBeInTheDocument()
  })
})
