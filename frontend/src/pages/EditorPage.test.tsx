import {render, screen, waitFor, within} from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import {MemoryRouter, Route, Routes} from 'react-router-dom'
import {beforeEach, describe, expect, it, vi} from 'vitest'

import {useEditorStore} from '../features/editor/store'
import {api} from '../services/api'
import type {ImagePage, ProcessingTask, Project, TextRegion} from '../types'
import {EditorPage} from './EditorPage'

const {confirmDialogMock, preloadImageMock} = vi.hoisted(() => ({
  confirmDialogMock: vi.fn(),
  preloadImageMock: vi.fn<(source: string) => Promise<void>>(),
}))

vi.mock('../features/editor/hooks/useImage', () => ({
  preloadImage: preloadImageMock,
}))

vi.mock('../components/AppShell', () => ({
  useAppHeaderSlots: () => ({editorTarget: null}),
}))

vi.mock('../components/GlobalDialog', () => ({
  useGlobalDialog: () => ({confirm: confirmDialogMock, isOpen: false}),
}))

vi.mock('../components/LoadingUI', () => ({
  BlockingLoader: () => null,
  ButtonLoading: ({label}: {label: string}) => <span>{label}</span>,
  CircularProgress: () => null,
  EditorSkeleton: () => <div>initial loading</div>,
  useMinimumLoadingTime: (loading: boolean) => loading,
}))

vi.mock('../features/editor/components/EditorToolbar', async importOriginal => ({
  ...await importOriginal<typeof import('../features/editor/components/EditorToolbar')>(),
  CanvasZoomControls: () => null,
}))

vi.mock('../features/editor/components/MangaCanvas', () => ({
  MangaCanvas: ({page}: {page: ImagePage}) => <output
    data-testid="current-page"
    data-page-id={page.id}
    data-clean-url={page.clean_url || ''}
    data-status={page.status}
  />,
}))

vi.mock('../features/editor/components/RegionProperties', () => ({
  RegionProperties: () => null,
}))

const project: Project = {
  id: 'project-1',
  name: 'Snapshot project',
  description: '',
  source_language: 'ja',
  target_language: 'zh',
  translation_context: {},
  settings: {},
  cover_url: null,
  page_count: 2,
  created_at: '2026-08-24T00:00:00Z',
  updated_at: '2026-08-24T00:00:00Z',
}

function page(id: string, patch: Partial<ImagePage> = {}): ImagePage {
  return {
    id,
    project_id: project.id,
    filename: `${id}.png`,
    width: 1200,
    height: 1800,
    order_index: id === 'page-a' ? 0 : 1,
    status: 'UPLOADED',
    current_stage: null,
    error_message: null,
    ocr_exempt: false,
    original_url: `/media/${id}-original.png?v=1`,
    clean_url: null,
    rendered_url: null,
    text_layer_url: null,
    created_at: '2026-08-24T00:00:00Z',
    updated_at: '2026-08-24T00:00:00Z',
    ...patch,
  }
}

function region(layoutData: Record<string, unknown>): TextRegion {
  return {
    id: 'region-1', image_id: 'page-a', region_key: 'R001',
    polygon: [[20, 20], [60, 20], [60, 100], [20, 100]], bbox: [20, 20, 40, 80],
    translated_polygon: [[20, 20], [60, 20], [60, 100], [20, 100]], translated_bbox: [20, 20, 40, 80],
    mask_url: null, source_text: '旧识别', translated_text: '旧译文', confidence: .9,
    orientation: 'vertical', reading_order: 1, panel_id: null, bubble_id: null,
    region_type: 'background_complex', font_size: 24, font_family: 'sans-serif', font_weight: 400,
    text_color: '#111111', stroke_color: '#ffffff', stroke_width: 0, alignment: 'center',
    line_spacing: 1.15, character_spacing: 0, rotation: 0, perspective_warp: false, opacity: 1,
    locked: false, visible: true, needs_review: false, review_reasons: [], layout_warning: false,
    layout_data: layoutData, updated_at: '2026-08-24T00:00:00Z',
  }
}

function deferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (reason?: unknown) => void
  const promise = new Promise<T>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise
    reject = rejectPromise
  })
  return {promise, resolve, reject}
}

function renderEditor(route = '/projects/project-1/editor/page-a') {
  return render(<MemoryRouter initialEntries={[route]}>
    <Routes>
      <Route path="/projects/:projectId/editor/:imageId" element={<EditorPage/>}/>
    </Routes>
  </MemoryRouter>)
}

describe('EditorPage page snapshot revalidation', () => {
  beforeEach(() => {
    useEditorStore.setState({
      view: 'translated',
      tool: 'select',
      zoom: 1,
      selectedIds: [],
      layers: {original: false, detection: true, clean: true, translated: true},
    })
    preloadImageMock.mockReset()
    preloadImageMock.mockResolvedValue(undefined)
    confirmDialogMock.mockReset()
    confirmDialogMock.mockResolvedValue(true)
    vi.spyOn(api.projects, 'get').mockResolvedValue(project)
    vi.spyOn(api.fonts, 'list').mockResolvedValue([])
    vi.spyOn(api.projects, 'fonts').mockResolvedValue([])
    vi.spyOn(api.images, 'regions').mockResolvedValue([])
    vi.spyOn(api.tasks, 'list').mockResolvedValue([])
  })

  it('shows the list snapshot immediately, then preloads and commits the authoritative page everywhere', async () => {
    const snapshot = page('page-a')
    const neighbor = page('page-b')
    const fresh = page('page-a', {
      status: 'INPAINTED',
      clean_url: '/media/page-a-clean.png?v=2',
      rendered_url: '/media/page-a-rendered.png?v=2',
      updated_at: '2026-08-24T00:01:00Z',
    })
    const authority = deferred<ImagePage>()
    const freshPreload = deferred<void>()
    vi.spyOn(api.projects, 'images').mockResolvedValue([snapshot, neighbor])
    vi.spyOn(api.images, 'get').mockReturnValue(authority.promise)
    preloadImageMock.mockImplementation(source => source === fresh.clean_url ? freshPreload.promise : Promise.resolve())
    const consoleError = vi.spyOn(console, 'error').mockImplementation(() => undefined)

    renderEditor()

    const current = await screen.findByTestId('current-page')
    expect(current).toHaveAttribute('data-page-id', snapshot.id)
    expect(current).toHaveAttribute('data-clean-url', '')
    expect(api.images.get).toHaveBeenCalledWith(snapshot.id)
    expect(useEditorStore.getState().view).toBe('original')
    expect(screen.getByRole('img', {name: snapshot.filename})).toHaveAttribute('fetchpriority', 'high')

    authority.resolve(fresh)
    await waitFor(() => expect(preloadImageMock).toHaveBeenCalledWith(fresh.clean_url))
    expect(current).toHaveAttribute('data-clean-url', '')
    expect(screen.getByRole('img', {name: snapshot.filename})).toHaveAttribute('src', snapshot.original_url)

    freshPreload.resolve()
    await waitFor(() => expect(current).toHaveAttribute('data-clean-url', fresh.clean_url))
    expect(current).toHaveAttribute('data-status', fresh.status)
    expect(useEditorStore.getState().view).toBe('translated')
    expect(screen.getByRole('img', {name: snapshot.filename})).toHaveAttribute('src', fresh.rendered_url)
    expect(screen.getByText('已修复')).toBeInTheDocument()
    expect(consoleError.mock.calls.flat().join(' ')).not.toContain('fetchPriority')
  })

  it('ignores a late authority response after a rapid route switch', async () => {
    const first = page('page-a')
    const second = page('page-b', {clean_url: '/media/page-b-clean.png?v=1'})
    const lateFirst = page('page-a', {clean_url: '/media/page-a-clean.png?v=2', rendered_url: '/media/page-a-rendered.png?v=2'})
    const firstAuthority = deferred<ImagePage>()
    vi.spyOn(api.projects, 'images').mockResolvedValue([first, second])
    vi.spyOn(api.images, 'get').mockImplementation(id => id === first.id ? firstAuthority.promise : Promise.resolve(second))

    renderEditor()
    const current = await screen.findByTestId('current-page')
    expect(current).toHaveAttribute('data-page-id', first.id)

    await userEvent.click(screen.getByRole('button', {name: new RegExp(second.filename)}))
    await waitFor(() => expect(current).toHaveAttribute('data-page-id', second.id))

    firstAuthority.resolve(lateFirst)
    await waitFor(() => expect(api.images.get).toHaveBeenCalledWith(second.id))
    expect(current).toHaveAttribute('data-page-id', second.id)
    expect(screen.getByRole('img', {name: first.filename})).toHaveAttribute('src', first.original_url)
    expect(preloadImageMock).not.toHaveBeenCalledWith(lateFirst.clean_url)
  })

  it('redetects legacy OCR regions so bubble grouping can migrate them', async () => {
    const snapshot = page('page-a', {status:'OCR_DONE'})
    const pendingTask = deferred<ProcessingTask>()
    vi.spyOn(api.projects, 'images').mockResolvedValue([snapshot])
    vi.spyOn(api.images, 'get').mockResolvedValue(snapshot)
    vi.mocked(api.images.regions).mockResolvedValue([region({detection:{}})])
    const process = vi.spyOn(api.images, 'process').mockReturnValue(pendingTask.promise)

    renderEditor()
    await screen.findByTestId('current-page')
    await userEvent.click(screen.getByRole('button', {name:'重新 OCR'}))

    await waitFor(() => expect(process).toHaveBeenCalledWith(snapshot.id, {
      start_stage: 'detection',
      end_stage: 'ocr',
      force: true,
      options: {crop_padding: 4},
    }))
  })

  it('allows background repair after OCR without requiring a translation first', async () => {
    const snapshot = page('page-a', {status:'OCR_DONE'})
    const recognizedRegion = region({
      detection:{balloon_assignment:{status:'assigned', bubble_id:'bubble-1'}},
    })
    recognizedRegion.translated_text = ''
    const pendingTask = deferred<ProcessingTask>()
    vi.spyOn(api.projects, 'images').mockResolvedValue([snapshot])
    vi.spyOn(api.images, 'get').mockResolvedValue(snapshot)
    vi.mocked(api.images.regions).mockResolvedValue([recognizedRegion])
    const process = vi.spyOn(api.images, 'process').mockReturnValue(pendingTask.promise)

    renderEditor()
    await screen.findByTestId('current-page')
    const repairButton = screen.getByRole('button', {name:'重新修复'})
    expect(repairButton).toBeEnabled()
    expect(screen.getByRole('button', {name:'重新排版'})).toBeDisabled()

    await userEvent.click(repairButton)
    await waitFor(() => expect(process).toHaveBeenCalledWith(snapshot.id, {
      start_stage: 'mask',
      end_stage: 'rendering',
      force: true,
      options: {rebuild_clean: true},
    }))
  })

  it('keeps background repair disabled before OCR completes', async () => {
    const snapshot = page('page-a', {status:'UPLOADED'})
    vi.spyOn(api.projects, 'images').mockResolvedValue([snapshot])
    vi.spyOn(api.images, 'get').mockResolvedValue(snapshot)

    renderEditor()
    await screen.findByTestId('current-page')
    expect(screen.getByRole('button', {name:'重新修复'})).toBeDisabled()
  })

  it('warns instead of claiming a merge when refreshed regions report bubble grouping unavailable', async () => {
    const snapshot = page('page-a', {status:'OCR_DONE'})
    const completedTask: ProcessingTask = {
      id: 'task-1', project_id: project.id, image_id: snapshot.id, region_id: null,
      task_type: 'pipeline', status: 'COMPLETED', progress: 1, current_stage: 'ocr',
      message: 'OCR complete', error_message: null, payload: {}, pause_requested: false,
      created_at: '2026-08-24T00:00:00Z', updated_at: '2026-08-24T00:01:00Z',
    }
    const legacyRegion = region({detection:{}})
    const unavailableRegion = region({
      detection:{balloon_assignment:{status:'unavailable', bubble_id:null, reason:'model_unavailable'}},
    })
    vi.spyOn(api.projects, 'images').mockResolvedValue([snapshot])
    vi.spyOn(api.images, 'get').mockResolvedValue(snapshot)
    vi.mocked(api.images.regions)
      .mockResolvedValueOnce([legacyRegion])
      .mockResolvedValue([unavailableRegion])
    vi.spyOn(api.images, 'process').mockResolvedValue(completedTask)
    vi.spyOn(api.tasks, 'get').mockResolvedValue(completedTask)

    renderEditor()
    await screen.findByTestId('current-page')
    await userEvent.click(screen.getByRole('button', {name:'重新 OCR'}))

    const toolbar = screen.getByRole('toolbar', {name:'编辑器顶部工具栏'})
    const warning = await within(toolbar).findByRole('alert')
    expect(toolbar).toHaveClass('h-12')
    expect(warning).not.toHaveClass('fixed')
    expect(warning).toHaveTextContent('本次 OCR 已完成，但气泡合并未执行')
    expect(warning).toHaveTextContent('气泡检测模型不可用或运行失败')
    expect(screen.queryByText(/请核对合并后的文字区域/)).not.toBeInTheDocument()
  })

  it('does not start legacy migration when the user cancels the warning', async () => {
    const snapshot = page('page-a', {status:'OCR_DONE'})
    vi.spyOn(api.projects, 'images').mockResolvedValue([snapshot])
    vi.spyOn(api.images, 'get').mockResolvedValue(snapshot)
    vi.mocked(api.images.regions).mockResolvedValue([region({detection:{}})])
    const process = vi.spyOn(api.images, 'process')
    confirmDialogMock.mockResolvedValueOnce(false)

    renderEditor()
    await screen.findByTestId('current-page')
    await userEvent.click(screen.getByRole('button', {name:'重新 OCR'}))

    await waitFor(() => expect(confirmDialogMock).toHaveBeenCalledOnce())
    expect(process).not.toHaveBeenCalled()
  })
})
