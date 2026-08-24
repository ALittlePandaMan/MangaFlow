import {render, screen, waitFor} from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import {MemoryRouter, Route, Routes} from 'react-router-dom'
import {beforeEach, describe, expect, it, vi} from 'vitest'

import {useEditorStore} from '../features/editor/store'
import {api} from '../services/api'
import type {ImagePage, Project} from '../types'
import {EditorPage} from './EditorPage'

const {preloadImageMock} = vi.hoisted(() => ({
  preloadImageMock: vi.fn<(source: string) => Promise<void>>(),
}))

vi.mock('../features/editor/hooks/useImage', () => ({
  preloadImage: preloadImageMock,
}))

vi.mock('../components/AppShell', () => ({
  useAppHeaderSlots: () => ({editorTarget: null}),
}))

vi.mock('../components/GlobalDialog', () => ({
  useGlobalDialog: () => ({confirm: vi.fn(async () => true), isOpen: false}),
}))

vi.mock('../components/LoadingUI', () => ({
  BlockingLoader: () => null,
  ButtonLoading: ({label}: {label: string}) => <span>{label}</span>,
  CircularProgress: () => null,
  EditorSkeleton: () => <div>initial loading</div>,
  useMinimumLoadingTime: (loading: boolean) => loading,
}))

vi.mock('../features/editor/components/EditorToolbar', () => ({
  CanvasZoomControls: () => null,
  EditorToolbar: ({rightActions}: {rightActions: React.ReactNode}) => <div>{rightActions}</div>,
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
})
