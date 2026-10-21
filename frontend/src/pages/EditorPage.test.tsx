import {act, fireEvent, render, screen, waitFor, within} from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import {Link, MemoryRouter, Route, Routes} from 'react-router-dom'
import {afterEach, beforeEach, describe, expect, it, vi} from 'vitest'

import {useEditorStore} from '../features/editor/store'
import {api} from '../services/api'
import {ImageUploadError} from '../services/imageUpload'
import type {EditorHistoryResult, EditorHistoryState, ImagePage, ProcessingTask, Project, TextRegion} from '../types'
import {EditorPage} from './EditorPage'
import {prepareMaskScene} from '../features/editor/lib/prepareMaskScene'

const {confirmDialogMock, openDialogMock, preloadImageMock, requestMaskLeaveMock, flushMaskMock, canvasPropsMock, propertiesPropsMock} = vi.hoisted(() => ({
  confirmDialogMock: vi.fn(),
  openDialogMock: vi.fn(),
  preloadImageMock: vi.fn<(source: string) => Promise<void>>(),
  requestMaskLeaveMock: vi.fn<(leave: () => void) => void>(),
  flushMaskMock: vi.fn<() => Promise<void>>(),
  canvasPropsMock: vi.fn(),
  propertiesPropsMock: vi.fn(),
}))
const headerSlots = vi.hoisted(() => ({editorTarget: null as HTMLDivElement | null}))

vi.mock('react-konva', () => ({Stage: () => null, Layer: () => null, Group: () => null, Image: () => null, Rect: () => null}))

vi.mock('../features/editor/hooks/useImage', () => ({
  preloadImage: preloadImageMock,
  useImage: () => ({image:null,loading:false,error:false}),
}))
vi.mock('../features/editor/lib/prepareMaskScene', () => ({prepareMaskScene:vi.fn()}))

vi.mock('../components/AppShell', () => ({
  useAppHeaderSlots: () => headerSlots,
}))

vi.mock('../components/GlobalDialog', () => ({
  useGlobalDialog: () => ({confirm: confirmDialogMock, openDialog: openDialogMock, isOpen: false}),
}))

vi.mock('../components/LoadingUI', async importOriginal => ({
  ...await importOriginal<typeof import('../components/LoadingUI')>(),
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
  TranslatedRegions: () => null,
  MangaCanvas: (props: {page: ImagePage,onPageContextMenu?: (position:{x:number,y:number}) => void}) => {canvasPropsMock(props); const {page} = props; return <output
    data-testid="current-page"
    data-page-id={page.id}
    data-clean-url={page.clean_url || ''}
    data-status={page.status}
    onContextMenu={event => {event.preventDefault(); props.onPageContextMenu?.({x:event.clientX,y:event.clientY})}}
  />},
}))

vi.mock('../features/editor/components/RegionProperties', () => ({
  RegionProperties: (props: unknown) => {propertiesPropsMock(props);return null},
}))

vi.mock('../features/editor/components/MaskEditor', async () => {
  const {forwardRef, useImperativeHandle} = await import('react')
  return {MaskEditor: forwardRef(function MockMaskEditor({region, tool, onSave, onStateChange, onHistoryBoundary,onHistoryStep}: {region: TextRegion, tool: string, onSave: (region: TextRegion, blob: Blob) => Promise<TextRegion>, onStateChange: (state: {undo:boolean,redo:boolean,dirty:boolean,busy:boolean}) => void,onHistoryBoundary:(direction:'undo'|'redo') => void,
    onHistoryStep?:(direction:'undo'|'redo',region:TextRegion,operationId:string) => Promise<TextRegion>}, ref) {
    useImperativeHandle(ref, () => ({requestLeave:requestMaskLeaveMock, flush:flushMaskMock, undo:() => onHistoryBoundary('undo'), redo:() => onHistoryBoundary('redo')}))
    return <section aria-label="test mask editor" data-page-id={region.image_id}><span>{region.mask_url}</span><span>{tool}</span><button onClick={() => void onSave(region, new Blob())}>Save test mask</button><button onClick={() => onStateChange({undo:false,redo:false,dirty:true,busy:true})}>Begin mask save</button><button onClick={() => onStateChange({undo:true,redo:false,dirty:true,busy:false})}>Begin auto mask save</button><button onClick={() => {void onHistoryStep?.('undo',region,'cached-mask-command').catch(() => {})}}>Undo cached mask</button></section>
  })}
})

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

function maskRepairTask(patch: Partial<ProcessingTask> = {}): ProcessingTask {
  return {id:'mask-repair-task',project_id:project.id,image_id:'page-a',region_id:null,
    task_type:'full',status:'COMPLETED',progress:1,current_stage:null,message:'Done',error_message:null,
    payload:{},pause_requested:false,created_at:'2026-10-14T00:00:00Z',updated_at:'2026-10-14T00:01:00Z',...patch}
}

function renderEditor(route = '/projects/project-1/editor/page-a', withHeader = false) {
  return render(<MemoryRouter initialEntries={[route]}>
    {withHeader && <Link to="/projects">Projects header</Link>}
    <Routes>
      <Route path="/projects/:projectId/editor/:imageId?" element={<EditorPage/>}/>
      {withHeader && <Route path="/projects" element={<output>Project index</output>}/>}
    </Routes>
  </MemoryRouter>)
}

async function openPageMenu(filename: string) {
  fireEvent.contextMenu(screen.getByRole('button',{name:new RegExp(filename)}), {clientX:40,clientY:120})
  return screen.findByRole('menu',{name:'图片操作'})
}

function mockDocumentHistory(snapshot: ImagePage, initial: TextRegion[], empty = false) {
  type Snapshot = {pages:ImagePage[],regions:TextRegion[]}
  let server: Snapshot = {pages:empty ? [] : [snapshot],regions:structuredClone(initial)}
  let cursor = 0, nextId = 0
  const entries: Array<{id:number,before:Snapshot,after:Snapshot,action:string,ids:string[]}> = []
  const state = (): EditorHistoryState => ({project_id:project.id,undo_count:cursor,redo_count:entries.length-cursor,
    undo_id:cursor ? entries[cursor-1].id : null,redo_id:entries[cursor]?.id || null,
    undo_label:cursor ? entries[cursor-1].action : null,redo_label:entries[cursor]?.action || null,pending:false})
  const commit = (action:string, ids:string[], mutate:(value:Snapshot) => void) => {
    const before = structuredClone(server)
    mutate(server)
    entries.splice(cursor)
    entries.push({id:++nextId,before,after:structuredClone(server),action,ids})
    cursor = entries.length
  }
  vi.spyOn(api.projects,'images').mockImplementation(async () => structuredClone(server.pages))
  vi.spyOn(api.images,'get').mockImplementation(async id => structuredClone(server.pages.find(item => item.id === id)!))
  vi.mocked(api.images.regions).mockImplementation(async id => structuredClone(server.regions.filter(item => item.image_id === id)))
  vi.mocked(api.history.state).mockImplementation(async () => state())
  const apply = vi.spyOn(api.history,'apply').mockImplementation(async (_id,direction,entryId) => {
    const index = direction === 'undo' ? cursor-1 : cursor
    const entry = entries[index]
    expect(entry.id).toBe(entryId)
    server = structuredClone(direction === 'undo' ? entry.before : entry.after)
    cursor += direction === 'undo' ? -1 : 1
    return {page:server.pages[0] || null,pages:server.pages,regions:server.regions,history:state(),region_ids:entry.ids,action:entry.action} satisfies EditorHistoryResult
  })
  return {state,apply,commit,regions:() => structuredClone(server.regions),page:() => structuredClone(server.pages[0])}
}

describe('EditorPage page snapshot revalidation', () => {
  afterEach(() => {headerSlots.editorTarget?.remove(); headerSlots.editorTarget = null})
  beforeEach(() => {
    useEditorStore.setState({
      view: 'translated',
      tool: 'select',
      zoom: 1,
      selectedIds: [],
      layers: {original: false, detection: true, clean: true, translated: true, mask:false},
    })
    preloadImageMock.mockReset()
    preloadImageMock.mockResolvedValue(undefined)
    vi.mocked(prepareMaskScene).mockReset()
    vi.mocked(prepareMaskScene).mockResolvedValue({} as Awaited<ReturnType<typeof prepareMaskScene>>)
    confirmDialogMock.mockReset()
    openDialogMock.mockReset()
    confirmDialogMock.mockResolvedValue(true)
    requestMaskLeaveMock.mockReset()
    flushMaskMock.mockReset(); flushMaskMock.mockResolvedValue(undefined)
    canvasPropsMock.mockClear()
    propertiesPropsMock.mockClear()
    requestMaskLeaveMock.mockImplementation(leave => leave())
    vi.spyOn(api.projects, 'get').mockResolvedValue(project)
    vi.spyOn(api.fonts, 'list').mockResolvedValue([])
    vi.spyOn(api.projects, 'fonts').mockResolvedValue([])
    vi.spyOn(api.images, 'regions').mockResolvedValue([])
    vi.spyOn(api.tasks, 'list').mockResolvedValue([])
    vi.spyOn(api.tasks, 'statuses').mockImplementation(ids => Promise.all(ids.map(id => api.tasks.get(id))))
    vi.spyOn(api.history,'state').mockResolvedValue({project_id:project.id,undo_count:0,redo_count:0,undo_id:null,redo_id:null,undo_label:null,redo_label:null,pending:false})
  })

  function showHeaderActions() {
    headerSlots.editorTarget = document.createElement('div')
    document.body.append(headerSlots.editorTarget)
  }

  it('matches the page heading and icons to the inspector header while keeping the import action', async () => {
    const current=page('page-a')
    vi.spyOn(api.projects,'images').mockResolvedValue([current])
    vi.spyOn(api.images,'get').mockResolvedValue(current)
    renderEditor()
    const heading=await screen.findByRole('heading',{name:'页面'})
    const header=heading.closest('header')!
    expect(header).toHaveClass('editor-pages-heading')
    expect(heading.parentElement).toHaveClass('editor-pages-heading-row')
    expect(heading.querySelector('svg')).toHaveAttribute('width','15')
    expect(heading.querySelector('svg')).toHaveAttribute('height','15')
    const button=within(header).getByRole('button',{name:'导入图片'})
    expect(button).toBeEnabled()
    expect(button).toHaveClass('editor-pages-import','icon-button','icon-button-compact')
    expect(button.querySelector('svg')).toHaveAttribute('width','16')
    const picker=vi.spyOn(HTMLInputElement.prototype,'click')
    fireEvent.click(button)
    expect(picker).toHaveBeenCalledOnce()
  })

  it('opens the requested page before a slow book index or font list has finished', async () => {
    const current = page('page-a')
    const index = deferred<ImagePage[]>(), fonts = deferred<never[]>()
    vi.spyOn(api.projects,'images').mockReturnValue(index.promise)
    vi.mocked(api.fonts.list).mockReturnValue(fonts.promise)
    vi.spyOn(api.images,'get').mockResolvedValue(current)
    renderEditor()
    await waitFor(() => expect(screen.getByTestId('current-page')).toHaveAttribute('data-page-id', current.id))
    expect(api.images.regions).toHaveBeenCalledOnce()
    expect(screen.queryByText('initial loading')).not.toBeInTheDocument()
    await act(async () => {index.resolve([current, page('page-b')]); fonts.resolve([])})
    expect(screen.getByRole('button',{name:/page-b.png/})).toBeInTheDocument()
    expect(api.images.regions).toHaveBeenCalledOnce()
    expect(api.images.get).toHaveBeenCalledOnce()
  })

  it('does not show a page from a different project while its index loads', async () => {
    vi.spyOn(api.projects,'images').mockResolvedValue([])
    vi.spyOn(api.images,'get').mockResolvedValue(page('page-a', {project_id:'another-project'}))
    renderEditor()
    await screen.findByText('该页面不属于当前项目')
    expect(screen.queryByTestId('current-page')).not.toBeInTheDocument()
  })

  it('shows adaptive import progress, warns before closing, and opens the first new page on success', async () => {
    const first = page('page-a'), added = page('new-page')
    vi.spyOn(api.projects, 'images').mockResolvedValueOnce([first]).mockResolvedValue([first, added])
    vi.spyOn(api.images, 'get').mockImplementation(async id => id === first.id ? first : added)
    const pending = deferred<ImagePage[]>()
    const upload = vi.spyOn(api.projects, 'upload').mockImplementation((_id, _files, progress) => {
      progress?.({completedFiles: 32, totalFiles: 320, completedBytes: 16, totalBytes: 160,
        batchFiles: 32, batchBytes: 16 * 1024 * 1024, resizing: false})
      return pending.promise
    })
    const view = renderEditor()
    await screen.findByTestId('current-page')
    const file = new File(['image'], 'new.png', {type: 'image/png'})
    fireEvent.change(view.container.querySelector('input[type="file"]')!, {target: {files: [file]}})
    await waitFor(() => expect(upload).toHaveBeenCalledWith(project.id, [file], expect.any(Function), expect.any(AbortSignal)))
    expect(screen.getByRole('status', {name: '正在导入图片 · 32/320 张'})).toHaveTextContent('当前批次 32 张，16.0 MiB')
    expect(screen.getByRole('progressbar', {name: '整体进度'})).toHaveAttribute('aria-valuenow', '10')
    const closing = new Event('beforeunload', {cancelable: true})
    window.dispatchEvent(closing)
    expect(closing.defaultPrevented).toBe(true)
    await act(async () => pending.resolve([added]))
    await waitFor(() => expect(screen.getByTestId('current-page')).toHaveAttribute('data-page-id', added.id))
    expect(screen.getByText('1 张图片已导入。')).toBeInTheDocument()
    const idleClosing = new Event('beforeunload', {cancelable: true})
    window.dispatchEvent(idleClosing)
    expect(idleClosing.defaultPrevented).toBe(false)
  })

  it('refreshes imported pages and undo history after a later upload batch fails without losing the current page', async () => {
    const first = page('page-a'), added = page('new-page')
    const list = vi.spyOn(api.projects, 'images').mockResolvedValueOnce([first]).mockResolvedValue([first, added])
    vi.spyOn(api.images, 'get').mockResolvedValue(first)
    vi.spyOn(api.projects, 'upload').mockRejectedValue(new ImageUploadError([added], 2, false,
      '已成功导入 1/2 张。剩余图片导入失败。', new Error('Rejected')))
    const view = renderEditor()
    await screen.findByTestId('current-page')
    const refreshes = vi.mocked(api.history.state).mock.calls.length
    fireEvent.change(view.container.querySelector('input[type="file"]')!, {target: {files: [new File(['image'], 'new.png')]}})
    await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent('已成功导入 1/2 张'))
    expect(list).toHaveBeenCalledTimes(2)
    expect(api.history.state).toHaveBeenCalledTimes(refreshes + 1)
    expect(screen.getByRole('button', {name: /new-page.png/})).toBeInTheDocument()
    expect(screen.getByTestId('current-page')).toHaveAttribute('data-page-id', first.id)
    expect(view.container.querySelector('input[type="file"]')).not.toBeDisabled()
  })

  it('reconciles the page list even when the first batch result is uncertain', async () => {
    const first = page('page-a'), added = page('new-page')
    const list = vi.spyOn(api.projects, 'images').mockResolvedValueOnce([first]).mockResolvedValue([first, added])
    vi.spyOn(api.images, 'get').mockResolvedValue(first)
    vi.spyOn(api.projects, 'upload').mockRejectedValue(new ImageUploadError([], 1, true,
      '本批次结果未能确认，请先检查页面列表，避免重复导入。', new TypeError('Failed to fetch')))
    const view = renderEditor()
    await screen.findByTestId('current-page')
    fireEvent.change(view.container.querySelector('input[type="file"]')!, {target: {files: [new File(['image'], 'new.png')]}})
    await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent('避免重复导入'))
    expect(list).toHaveBeenCalledTimes(2)
    expect(screen.getByRole('button', {name: /new-page.png/})).toBeInTheDocument()
  })

  it('aborts remaining image imports when the editor is unmounted', async () => {
    const first = page('page-a')
    vi.spyOn(api.projects, 'images').mockResolvedValue([first])
    vi.spyOn(api.images, 'get').mockResolvedValue(first)
    const pending = deferred<ImagePage[]>()
    const upload = vi.spyOn(api.projects, 'upload').mockReturnValue(pending.promise)
    const view = renderEditor()
    await screen.findByTestId('current-page')
    fireEvent.change(view.container.querySelector('input[type="file"]')!, {target: {files: [new File(['image'], 'new.png')]}})
    await waitFor(() => expect(upload).toHaveBeenCalledOnce())
    const signal = upload.mock.calls[0][3]!
    expect(signal.aborted).toBe(false)
    view.unmount()
    expect(signal.aborted).toBe(true)
    await act(async () => pending.resolve([page('new-page')]))
    expect(api.projects.images).toHaveBeenCalledOnce()
  })

  it('reports a completed import accurately if the final page-list refresh fails', async () => {
    const first = page('page-a'), added = page('new-page')
    vi.spyOn(api.projects, 'images').mockResolvedValueOnce([first]).mockRejectedValue(new TypeError('Failed to fetch'))
    vi.spyOn(api.images, 'get').mockResolvedValue(first)
    vi.spyOn(api.projects, 'upload').mockResolvedValue([added])
    const view = renderEditor()
    await screen.findByTestId('current-page')
    fireEvent.change(view.container.querySelector('input[type="file"]')!, {target: {files: [new File(['image'], 'new.png')]}})
    await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent('1 张图片已导入，但页面列表刷新失败'))
    expect(api.projects.upload).toHaveBeenCalledOnce()
  })

  it('disables immersive reading when the project has no images', async () => {
    showHeaderActions()
    vi.spyOn(api.projects,'images').mockResolvedValue([])
    renderEditor('/projects/project-1/editor')
    await screen.findByRole('button',{name:'选择图片'})
    expect(screen.getByRole('button',{name:'沉浸式阅读'})).toBeDisabled()
  })

  it('opens immersive reading from project actions and returns to the same editor without mutations', async () => {
    showHeaderActions()
    vi.stubGlobal('ResizeObserver',class {observe() {} disconnect() {}})
    const first=page('page-a',{clean_url:'/clean.png'})
    vi.spyOn(api.projects,'images').mockResolvedValue([first])
    vi.spyOn(api.images,'get').mockResolvedValue(first)
    vi.mocked(api.images.regions).mockResolvedValue([region({})])
    const update=vi.spyOn(api.regions,'update')
    renderEditor();await screen.findByTestId('current-page')
    const before=useEditorStore.getState()
    fireEvent.click(screen.getByRole('button',{name:'沉浸式阅读'}))
    const reader=screen.getByRole('dialog',{name:'沉浸式阅读'})
    expect(reader).toHaveAttribute('data-page-id','page-a')
    expect(screen.queryByTestId('current-page')).not.toBeInTheDocument()
    fireEvent.keyDown(reader,{key:'Delete'})
    fireEvent.keyDown(reader,{key:'z',ctrlKey:true})
    expect(update).not.toHaveBeenCalled()
    fireEvent.click(reader)
    expect(screen.queryByRole('dialog',{name:'沉浸式阅读'})).not.toBeInTheDocument()
    expect(screen.getByTestId('current-page')).toHaveAttribute('data-page-id','page-a')
    expect(useEditorStore.getState()).toEqual(before)
    vi.unstubAllGlobals()
  })

  it('uses guarded page navigation for reader wheel turns and stays in reading mode', async () => {
    showHeaderActions()
    vi.stubGlobal('ResizeObserver',class {observe() {} disconnect() {}})
    const first=page('page-a'), second=page('page-b')
    vi.spyOn(api.projects,'images').mockResolvedValue([first,second])
    vi.spyOn(api.images,'get').mockResolvedValue(first)
    renderEditor();await screen.findByTestId('current-page')
    fireEvent.click(screen.getByRole('button',{name:'沉浸式阅读'}))
    fireEvent.wheel(screen.getByRole('dialog'),{deltaY:120})
    await waitFor(() => expect(screen.getByRole('dialog')).toHaveAttribute('data-page-id','page-b'))
    expect(screen.queryByTestId('current-page')).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('dialog'))
    expect(screen.getByTestId('current-page')).toHaveAttribute('data-page-id','page-b')
    vi.unstubAllGlobals()
  })

  it.each([
    {currentCount: 0, liveCount: 0, otherCount: 0, enabled: false},
    {currentCount: 1, liveCount: 0, otherCount: 0, enabled: false},
    {currentCount: 0, liveCount: 1, otherCount: 0, enabled: true},
    {currentCount: 0, liveCount: 0, otherCount: 1, enabled: true},
  ])('gates batch repair by actual boxes: $currentCount / $liveCount / $otherCount', async ({currentCount, liveCount, otherCount, enabled}) => {
    showHeaderActions()
    const current = page('page-a', {region_count: currentCount})
    vi.spyOn(api.projects,'images').mockResolvedValue([current, page('page-b', {region_count: otherCount, ocr_exempt: true})])
    vi.spyOn(api.images,'get').mockResolvedValue(current)
    vi.mocked(api.images.regions).mockResolvedValue(liveCount ? [region({})] : [])
    renderEditor(); await screen.findByTestId('current-page')
    const button = screen.getByRole('button', {name:'批量修复并翻译'})
    if (enabled) expect(button).toBeEnabled()
    else expect(button).toBeDisabled()
  })

  it('batch repairs and translates only pages with boxes and refreshes the current result', async () => {
    showHeaderActions()
    const current = page('page-a', {region_count:1})
    const second = page('page-b', {region_count:1})
    const empty = page('page-c', {region_count:0})
    const updated = {...region({}), source_text:'补识别原文', translated_text:'新译文'}
    vi.spyOn(api.projects,'images').mockResolvedValue([current, second, empty])
    const getPage = vi.spyOn(api.images,'get').mockResolvedValue(current)
    vi.mocked(api.images.regions).mockResolvedValue([{...region({}), source_text:''}])
    const tasks = [maskRepairTask({id:'batch-a'}), maskRepairTask({id:'batch-b',image_id:'page-b'})]
    const enqueue = vi.spyOn(api.projects,'batch').mockResolvedValue(tasks)
    const completion = deferred<ProcessingTask>()
    vi.spyOn(api.tasks,'get').mockImplementation(id => id === 'batch-a' ? completion.promise : Promise.resolve(tasks[1]))
    renderEditor(); await screen.findByTestId('current-page')
    fireEvent.click(screen.getByRole('button',{name:'批量修复并翻译'}))
    await waitFor(() => expect(enqueue).toHaveBeenCalledWith(project.id, {
      start_stage:'translation',end_stage:'rendering',force:true,
      options:{rebuild_clean:true,repair_geometry:'source'},
      image_ids:['page-a','page-b'],only_with_regions:true,
    }))
    expect(screen.getByRole('button',{name:'批量识别'})).toBeDisabled()
    expect(screen.getAllByRole('status',{name:'正在批量修复并翻译…'}).length).toBeGreaterThan(0)
    expect(screen.queryByRole('button',{name:'查看任务 / 暂停'})).not.toBeInTheDocument()
    expect(screen.queryByRole('button',{name:'任务'})).not.toBeInTheDocument()
    expect(openDialogMock).not.toHaveBeenCalled()
    getPage.mockResolvedValue({...current,clean_url:'/clean-new.png',rendered_url:'/translated-new.png'})
    vi.mocked(api.images.regions).mockResolvedValue([updated])
    await act(async () => {completion.resolve(tasks[0])})
    await screen.findByText(/批量修复并翻译完成，共 2 页/)
    expect(canvasPropsMock.mock.lastCall![0].regions).toEqual([updated])
    expect(useEditorStore.getState().view).toBe('translated')
    expect(screen.getByRole('button',{name:'批量修复并翻译'})).toBeEnabled()
  })

  it.each(['FAILED','CANCELLED'])('does not report batch success for %s pages', async status => {
    showHeaderActions()
    const current = page('page-a', {region_count:1})
    vi.spyOn(api.projects,'images').mockResolvedValue([current])
    vi.spyOn(api.images,'get').mockResolvedValue(current)
    vi.mocked(api.images.regions).mockResolvedValue([region({})])
    const task = maskRepairTask({status})
    vi.spyOn(api.projects,'batch').mockResolvedValue([task])
    vi.spyOn(api.tasks,'get').mockResolvedValue(task)
    renderEditor(); await screen.findByTestId('current-page')
    fireEvent.click(screen.getByRole('button',{name:'批量修复并翻译'}))
    await screen.findByText(/0\/1 页完成/)
    expect(screen.queryByText(/批量修复并翻译完成，共/)).not.toBeInTheDocument()
  })

  it('does not enqueue when the batch confirmation is cancelled', async () => {
    showHeaderActions()
    const current = page('page-a')
    vi.spyOn(api.projects,'images').mockResolvedValue([current])
    vi.spyOn(api.images,'get').mockResolvedValue(current)
    vi.mocked(api.images.regions).mockResolvedValue([region({})])
    const enqueue = vi.spyOn(api.projects,'batch').mockResolvedValue([])
    confirmDialogMock.mockResolvedValue(false)
    renderEditor(); await screen.findByTestId('current-page')
    fireEvent.click(screen.getByRole('button',{name:'批量修复并翻译'}))
    await waitFor(() => expect(confirmDialogMock).toHaveBeenCalledOnce())
    expect(enqueue).not.toHaveBeenCalled()
  })

  it('waits for creation to finish before inserting and selecting the saved box',async () => {
    const snapshot=page('page-a',{rendered_url:'/render.png',text_layer_url:'/text.png'})
    vi.spyOn(api.projects,'images').mockResolvedValue([snapshot]);vi.spyOn(api.images,'get').mockResolvedValue(snapshot)
    const response=deferred<TextRegion>(),create=vi.spyOn(api.regions,'create').mockReturnValue(response.promise)
    renderEditor();await screen.findByTestId('current-page')
    const polygon=[[10,10],[60,10],[60,100],[10,100]],bbox=[10,10,50,90]
    let save!:Promise<boolean>
    act(() => {save=canvasPropsMock.mock.lastCall![0].onCreate(polygon,bbox)})
    await waitFor(()=>expect(create).toHaveBeenCalledOnce())
    expect(canvasPropsMock.mock.lastCall![0].regions).toEqual([])
    expect(useEditorStore.getState().selectedIds).toEqual([])
    expect(screen.getAllByRole('status',{name:'正在创建文字区域…'}).some(item=>item.classList.contains('fixed'))).toBe(true)
    expect(screen.getByRole('button',{name:'选择 / 变换'})).toBeDisabled()
    const created={...region({}),id:create.mock.calls[0][1].id!,polygon,bbox}
    await act(async () => {response.resolve(created);expect(await save).toBe(true)})
    expect(canvasPropsMock.mock.lastCall![0].regions).toEqual([created])
    expect(useEditorStore.getState().selectedIds).toEqual([created.id])
    expect(screen.getByRole('button',{name:'选择 / 变换'})).toBeEnabled()
  })

  it('never inserts a temporary box when creation fails and reports the failure',async () => {
    const snapshot=page('page-a'),initial=region({})
    vi.spyOn(api.projects,'images').mockResolvedValue([snapshot]);vi.spyOn(api.images,'get').mockResolvedValue(snapshot)
    vi.mocked(api.images.regions).mockResolvedValue([initial])
    const response=deferred<TextRegion>();vi.spyOn(api.regions,'create').mockReturnValue(response.promise)
    renderEditor();await screen.findByTestId('current-page')
    let save!:Promise<boolean>
    act(() => {save=canvasPropsMock.mock.lastCall![0].onCreate([[10,10],[60,10],[30,100]],[10,10,50,90])})
    await waitFor(()=>expect(api.regions.create).toHaveBeenCalledOnce())
    expect(canvasPropsMock.mock.lastCall![0].regions).toEqual([initial])
    await act(async () => {response.reject(new Error('creation failed'));expect(await save).toBe(false)})
    await waitFor(()=>expect(screen.getByRole('button',{name:'选择 / 变换'})).toBeEnabled())
    expect(canvasPropsMock.mock.lastCall![0].regions).toEqual([initial])
    expect(screen.getByText('creation failed')).toBeInTheDocument()
  })

  it.each(['source','translated'])('adopts a %s frame edit without blocking on persistence',async frame => {
    const snapshot=page('page-a'),initial=region({}),journal=mockDocumentHistory(snapshot,[initial])
    const response=deferred<TextRegion>(),update=vi.spyOn(api.regions,'update').mockReturnValue(response.promise)
    renderEditor();await screen.findByTestId('current-page')
    const geometry={polygon:[[40,20],[80,20],[80,100],[40,100]],bbox:[40,20,40,80]}
    const patch=frame==='source'?geometry:{translated_polygon:geometry.polygon,translated_bbox:geometry.bbox}
    let save!:Promise<void>
    act(()=>{save=canvasPropsMock.mock.lastCall![0].onUpdate(initial.id,patch)})
    await waitFor(()=>expect(update).toHaveBeenCalledOnce())
    expect(canvasPropsMock.mock.lastCall![0].regions[0]).toMatchObject(patch)
    expect(screen.queryByRole('status',{name:'正在保存文字区域…'})).not.toBeInTheDocument()
    expect(screen.getByRole('button',{name:'矩形区域'})).toBeEnabled()
    expect(canvasPropsMock.mock.lastCall![0].runningAction).toBeNull()
    expect(propertiesPropsMock.mock.lastCall![0].busyAction).toBeNull()
    await act(async()=>{
      journal.commit('调整文字区域',[initial.id],value=>{value.regions=[{...initial,...patch}]})
      response.resolve({...journal.regions()[0],editor_history:journal.state()});await save
    })
    expect(canvasPropsMock.mock.lastCall![0].regions[0]).toMatchObject(patch)
    expect(screen.getByRole('button',{name:'矩形区域'})).toBeEnabled()
  })

  it('adopts bulk geometry immediately and uses the authoritative journal for undo and redo',async()=>{
    const snapshot=page('page-a'),initial=region({}),journal=mockDocumentHistory(snapshot,[initial]),gate=deferred<void>()
    const patch={bbox:[40,20,40,80],polygon:[[40,20],[80,20],[80,100],[40,100]]}
    vi.spyOn(api.regions,'updateMany').mockImplementation(async changes=>{
      await gate.promise
      journal.commit('批量编辑文字区域',changes.map(change=>change.id),value=>{value.regions=value.regions.map(item=>({...item,...changes.find(change=>change.id===item.id)?.patch}))})
      return journal.regions().map(item=>({...item,editor_history:journal.state()}))
    })
    renderEditor();await screen.findByTestId('current-page')
    let save!:Promise<void>
    act(()=>{save=canvasPropsMock.mock.lastCall![0].onUpdateMany([{id:initial.id,patch}])})
    await waitFor(()=>expect(api.regions.updateMany).toHaveBeenCalledOnce())
    expect(canvasPropsMock.mock.lastCall![0].regions[0].bbox).toEqual(patch.bbox)
    expect(screen.getByRole('button',{name:'矩形区域'})).toBeEnabled()
    await act(async()=>{gate.resolve();await save})
    const actualApply=journal.apply.getMockImplementation()!
    for (const direction of ['undo','redo'] as const) {
      const response=deferred<EditorHistoryResult>();journal.apply.mockReturnValueOnce(response.promise)
      const before=direction==='undo'?patch.bbox:initial.bbox,after=direction==='undo'?initial.bbox:patch.bbox
      await userEvent.click(screen.getByRole('button',{name:direction==='undo'?'撤销':'重做'}))
      await waitFor(()=>expect(journal.apply).toHaveBeenCalledTimes(direction==='undo'?1:2))
      expect(canvasPropsMock.mock.lastCall![0].regions[0].bbox).toEqual(before)
      const label=direction==='undo'?'正在撤销上一步…':'正在重做上一步…'
      expect(screen.getAllByRole('status',{name:label}).some(item=>item.classList.contains('fixed'))).toBe(true)
      expect(screen.getByRole('button',{name:'矩形区域'})).toBeDisabled()
      await act(async()=>response.resolve(await actualApply(project.id,direction,(direction==='undo'?journal.state().undo_id:journal.state().redo_id)!,snapshot.id)))
      await waitFor(()=>expect(canvasPropsMock.mock.lastCall![0].regions[0].bbox).toEqual(after))
      await waitFor(()=>expect(screen.getByRole('button',{name:'矩形区域'})).toBeEnabled())
    }
  })

  it('keeps consecutive drags editable and ignores older geometry responses',async()=>{
    const initial=region({}),journal=mockDocumentHistory(page('page-a'),[initial])
    const gates=[deferred<void>(),deferred<void>()]
    let call=0
    const update=vi.spyOn(api.regions,'update').mockImplementation(async(id,patch)=>{
      await gates[call++].promise
      journal.commit('移动文字区域',[id],value=>{value.regions[0]={...value.regions[0],...patch}})
      return {...journal.regions()[0],editor_history:journal.state()}
    })
    renderEditor();await screen.findByTestId('current-page')
    const first={bbox:[40,20,40,80]},second={bbox:[80,20,40,80]}
    await act(async()=>{await canvasPropsMock.mock.lastCall![0].onUpdate(initial.id,first)})
    await act(async()=>{await canvasPropsMock.mock.lastCall![0].onUpdate(initial.id,second)})
    expect(update).toHaveBeenCalledOnce()
    expect(canvasPropsMock.mock.lastCall![0].regions[0].bbox).toEqual(second.bbox)
    expect(screen.getByRole('button',{name:'选择 / 变换'})).toBeEnabled()
    await act(async()=>gates[0].resolve())
    await waitFor(()=>expect(update).toHaveBeenCalledTimes(2))
    expect(canvasPropsMock.mock.lastCall![0].regions[0].bbox).toEqual(second.bbox)
    expect(journal.regions()[0].bbox).toEqual(first.bbox)
    await act(async()=>gates[1].resolve())
    await waitFor(()=>expect(journal.state().undo_count).toBe(2))
    await userEvent.click(screen.getByRole('button',{name:'撤销'}))
    await waitFor(()=>expect(canvasPropsMock.mock.lastCall![0].regions[0].bbox).toEqual(first.bbox))
  })

  it('navigates A/D in page order, stops at either end and ignores held keys and typing',async()=>{
    const first=page('page-a'),second=page('page-b')
    vi.spyOn(api.projects,'images').mockResolvedValue([first,second])
    vi.spyOn(api.images,'get').mockImplementation(async id=>id===first.id?first:second)
    renderEditor();await screen.findByTestId('current-page')
    fireEvent.keyDown(window,{key:'a',code:'KeyA'})
    expect(screen.getByTestId('current-page')).toHaveAttribute('data-page-id',first.id)
    fireEvent.keyDown(window,{key:'d',code:'KeyD',repeat:true})
    fireEvent.keyDown(window,{key:'d',code:'KeyD',isComposing:true})
    expect(screen.getByTestId('current-page')).toHaveAttribute('data-page-id',first.id)
    const textarea=document.createElement('textarea');document.body.append(textarea)
    try {fireEvent.keyDown(textarea,{key:'d',code:'KeyD'})}
    finally {textarea.remove()}
    expect(screen.getByTestId('current-page')).toHaveAttribute('data-page-id',first.id)
    fireEvent.keyDown(window,{key:'d',code:'KeyD'})
    await waitFor(()=>expect(screen.getByTestId('current-page')).toHaveAttribute('data-page-id',second.id))
    fireEvent.keyDown(window,{key:'d',code:'KeyD'})
    expect(screen.getByTestId('current-page')).toHaveAttribute('data-page-id',second.id)
    fireEvent.keyDown(window,{key:'a',code:'KeyA'})
    await waitFor(()=>expect(screen.getByTestId('current-page')).toHaveAttribute('data-page-id',first.id))
  })

  it('keeps typing and coalesces form fields while a previous attribute save is in flight',async()=>{
    const initial=region({}),journal=mockDocumentHistory(page('page-a'),[initial]),gate=deferred<void>()
    let call=0
    const update=vi.spyOn(api.regions,'update').mockImplementation(async(id,patch)=>{
      if (call++===0) await gate.promise
      journal.commit('编辑文字区域',[id],value=>{value.regions[0]={...value.regions[0],...patch}})
      return {...journal.regions()[0],editor_history:journal.state()}
    })
    renderEditor();await screen.findByTestId('current-page')
    act(()=>{void propertiesPropsMock.mock.lastCall![0].onUpdate(initial.id,{font_size:30})})
    await waitFor(()=>expect(update).toHaveBeenCalledOnce())
    act(()=>{void propertiesPropsMock.mock.lastCall![0].onUpdate(initial.id,{font_size:34})})
    act(()=>{void propertiesPropsMock.mock.lastCall![0].onUpdate(initial.id,{translated_text:'连续输入的新译文',alignment:'left'})})
    const expected={font_size:34,translated_text:'连续输入的新译文',alignment:'left'}
    expect(canvasPropsMock.mock.lastCall![0].regions[0]).toMatchObject(expected)
    expect(propertiesPropsMock.mock.lastCall![0].busyAction).toBeNull()
    await act(async()=>gate.resolve())
    expect(canvasPropsMock.mock.lastCall![0].regions[0]).toMatchObject(expected)
    await waitFor(()=>expect(update).toHaveBeenCalledTimes(2))
    expect(update.mock.calls[1]).toEqual([initial.id,expected])
    await waitFor(()=>expect(journal.regions()[0]).toMatchObject(expected))
  })

  it('does not overwrite a new individual edit when an older bulk alignment completes',async()=>{
    const first=region({}),second={...region({}),id:'region-2',region_key:'R002'}
    const journal=mockDocumentHistory(page('page-a'),[first,second]),bulk=deferred<void>(),single=deferred<void>()
    vi.spyOn(api.regions,'updateMany').mockImplementation(async changes=>{
      await bulk.promise
      journal.commit('批量对齐',changes.map(change=>change.id),value=>{value.regions=value.regions.map(item=>({...item,...changes.find(change=>change.id===item.id)?.patch}))})
      return journal.regions().map(item=>({...item,editor_history:journal.state()}))
    })
    const update=vi.spyOn(api.regions,'update').mockImplementation(async(id,patch)=>{
      await single.promise
      journal.commit('编辑文字区域',[id],value=>{value.regions[0]={...value.regions[0],...patch}})
      return {...journal.regions()[0],editor_history:journal.state()}
    })
    renderEditor();await screen.findByTestId('current-page')
    const aligned={bbox:[80,20,40,80]},latest={bbox:[100,20,40,80],rotation:15}
    await act(async()=>{await canvasPropsMock.mock.lastCall![0].onUpdateMany([first,second].map(item=>({id:item.id,patch:aligned})))})
    expect(canvasPropsMock.mock.lastCall![0].regions.map((item:TextRegion)=>item.bbox)).toEqual([aligned.bbox,aligned.bbox])
    await act(async()=>{await canvasPropsMock.mock.lastCall![0].onUpdate(first.id,latest)})
    expect(update).not.toHaveBeenCalled()
    await act(async()=>bulk.resolve())
    await waitFor(()=>expect(update).toHaveBeenCalledOnce())
    expect(canvasPropsMock.mock.lastCall![0].regions[0]).toMatchObject(latest)
    expect(canvasPropsMock.mock.lastCall![0].regions[1].bbox).toEqual(aligned.bbox)
    await act(async()=>single.resolve())
  })

  it.each(['page','header','shortcut'])('flushes both in-flight and debounced edits before %s navigation',async destination=>{
    const first=page('page-a'),second=page('page-b'),initial=region({}),gate=deferred<void>()
    vi.spyOn(api.projects,'images').mockResolvedValue([first,second])
    vi.spyOn(api.images,'get').mockImplementation(async id=>id===first.id?first:second)
    vi.mocked(api.images.regions).mockImplementation(async id=>id===first.id?[initial]:[])
    const update=vi.spyOn(api.regions,'update').mockImplementation(async(_id,patch)=>{await gate.promise;return {...initial,...patch}})
    renderEditor(undefined,true);await screen.findByTestId('current-page')
    await act(async()=>{await canvasPropsMock.mock.lastCall![0].onUpdate(initial.id,{bbox:[80,20,40,80]})})
    act(()=>{void propertiesPropsMock.mock.lastCall![0].onUpdate(initial.id,{font_size:35})})
    if (destination==='shortcut') fireEvent.keyDown(window,{key:'d',code:'KeyD'})
    else await userEvent.click(destination==='page'?screen.getByRole('button',{name:new RegExp(second.filename)}):screen.getByRole('link',{name:'Projects header'}))
    expect(screen.getByTestId('current-page')).toHaveAttribute('data-page-id',first.id)
    expect(screen.getByRole('button',{name:'选择 / 变换'})).toBeDisabled()
    expect(update).toHaveBeenCalledOnce()
    await act(async()=>gate.resolve())
    await waitFor(()=>expect(update).toHaveBeenCalledTimes(2))
    if (destination!=='header') await waitFor(()=>expect(screen.getByTestId('current-page')).toHaveAttribute('data-page-id',second.id))
    else await screen.findByText('Project index')
  })

  it('stops queued edits and dependent processing after a save fails, then restores saved state',async()=>{
    const initial=region({}),journal=mockDocumentHistory(page('page-a'),[initial]),gate=deferred<TextRegion>()
    const update=vi.spyOn(api.regions,'update').mockReturnValue(gate.promise)
    const stage=vi.spyOn(api.regions,'stage')
    renderEditor();await screen.findByTestId('current-page')
    await act(async()=>{await canvasPropsMock.mock.lastCall![0].onUpdate(initial.id,{bbox:[80,20,40,80]})})
    await act(async()=>{await canvasPropsMock.mock.lastCall![0].onUpdate(initial.id,{bbox:[100,20,40,80]})})
    act(()=>{canvasPropsMock.mock.lastCall![0].onRegionAction([initial.id],'ocr')})
    expect(stage).not.toHaveBeenCalled()
    await act(async()=>gate.reject(new Error('save failed')))
    await waitFor(()=>expect(screen.getByRole('button',{name:'选择 / 变换'})).toBeEnabled())
    expect(update).toHaveBeenCalledOnce()
    expect(stage).not.toHaveBeenCalled()
    expect(canvasPropsMock.mock.lastCall![0].regions).toEqual(journal.regions())
    expect(screen.getByText('save failed')).toBeInTheDocument()
  })

  it('accepts repeated keyboard nudges without losing increments during a slow bulk save',async()=>{
    const initial=region({}),journal=mockDocumentHistory(page('page-a'),[initial]),gate=deferred<void>()
    const update=vi.spyOn(api.regions,'updateMany').mockImplementation(async changes=>{
      await gate.promise
      journal.commit('微调文字区域',changes.map(change=>change.id),value=>{value.regions=value.regions.map(item=>({...item,...changes.find(change=>change.id===item.id)?.patch}))})
      return journal.regions().map(item=>({...item,editor_history:journal.state()}))
    })
    renderEditor();await screen.findByTestId('current-page')
    act(()=>useEditorStore.getState().select(initial.id))
    for (let i=0;i<3;i++) fireEvent.keyDown(window,{key:'ArrowRight',code:'ArrowRight',repeat:i>0})
    expect(canvasPropsMock.mock.lastCall![0].regions[0].bbox).toEqual([23,20,40,80])
    await waitFor(()=>expect(update).toHaveBeenCalledOnce())
    await act(async()=>gate.resolve())
    await waitFor(()=>expect(journal.regions()[0].bbox).toEqual([23,20,40,80]))
    expect(update).toHaveBeenCalledTimes(3)
  })

  it('blocks new edits during a history restore and keeps the current geometry until it finishes',async()=>{
    const snapshot=page('page-a'),initial=region({}),journal=mockDocumentHistory(snapshot,[initial])
    const update=vi.spyOn(api.regions,'update').mockImplementation(async(id,patch)=>{
      journal.commit('移动文字区域',[id],value=>{value.regions=value.regions.map(item=>({...item,...patch}))})
      return {...journal.regions()[0],editor_history:journal.state()}
    })
    renderEditor();await screen.findByTestId('current-page')
    await act(async()=>{await canvasPropsMock.mock.lastCall![0].onUpdate(initial.id,{bbox:[40,20,40,80]})})
    const actualApply=journal.apply.getMockImplementation()!,gate=deferred<void>()
    journal.apply.mockImplementation(async(...args)=>{await gate.promise;return actualApply(...args)})
    await userEvent.click(screen.getByRole('button',{name:'撤销'}))
    await waitFor(()=>expect(journal.apply).toHaveBeenCalledOnce())
    expect(canvasPropsMock.mock.lastCall![0].regions[0].bbox).toEqual([40,20,40,80])
    expect(screen.getByRole('button',{name:'选择 / 变换'})).toBeDisabled()
    await act(async()=>{await canvasPropsMock.mock.lastCall![0].onUpdate(initial.id,{bbox:[70,20,40,80]})})
    expect(update).toHaveBeenCalledOnce()
    expect(canvasPropsMock.mock.lastCall![0].runningAction).toBe('history')
    await act(async()=>gate.resolve())
    await waitFor(()=>expect(canvasPropsMock.mock.lastCall![0].regions[0].bbox).toEqual(initial.bbox))
    expect(screen.getByRole('button',{name:'选择 / 变换'})).toBeEnabled()
  })

  it('keeps boxes visible during deletion and rejects another delete or edit until completion',async()=>{
    const snapshot=page('page-a',{clean_url:'/clean.png'}),first=region({}),second={...region({}),id:'region-2',region_key:'R002'}
    const journal=mockDocumentHistory(snapshot,[first,second]),gate=deferred<void>()
    vi.spyOn(api.regions,'remove').mockImplementation(async id=>{
      await gate.promise;journal.commit('删除文字区域',[id],value=>{value.regions=value.regions.filter(item=>item.id!==id)})
      return {region_id:id,rebuild_task:null,page:{...snapshot,clean_url:'/delete-clean.png'},editor_history:journal.state()}
    })
    const update=vi.spyOn(api.regions,'update')
    renderEditor();await screen.findByTestId('current-page')
    act(()=>{useEditorStore.getState().select(first.id);canvasPropsMock.mock.lastCall![0].onRegionAction([first.id],'delete')})
    await waitFor(()=>expect(api.regions.remove).toHaveBeenCalledOnce())
    expect(canvasPropsMock.mock.lastCall![0].regions).toEqual([first,second])
    expect(useEditorStore.getState().selectedIds).toEqual([first.id])
    expect(screen.getAllByRole('status',{name:'正在删除所选区域…'}).some(item=>item.classList.contains('fixed'))).toBe(true)
    expect(screen.getByRole('button',{name:'矩形区域'})).toBeDisabled()
    act(()=>canvasPropsMock.mock.lastCall![0].onRegionAction([second.id],'delete'))
    await act(async()=>{await canvasPropsMock.mock.lastCall![0].onUpdate(second.id,{translated_text:'newer'})})
    expect(api.regions.remove).toHaveBeenCalledOnce();expect(update).not.toHaveBeenCalled()
    await act(async()=>gate.resolve())
    await waitFor(()=>expect(canvasPropsMock.mock.lastCall![0].regions).toEqual([second]))
    expect(screen.getByRole('button',{name:'选择 / 变换'})).toBeEnabled()
  })

  it('keeps the wait dialog and old scene until the deletion background is decoded',async()=>{
    const snapshot=page('page-a',{clean_url:'/old-clean.png'}),initial=region({})
    vi.spyOn(api.projects,'images').mockResolvedValue([snapshot]);vi.spyOn(api.images,'get').mockResolvedValue(snapshot)
    vi.mocked(api.images.regions).mockResolvedValue([initial])
    const decoded=deferred<void>()
    preloadImageMock.mockImplementation(source=>source==='/new-clean.png'?decoded.promise:Promise.resolve())
    vi.spyOn(api.regions,'remove').mockResolvedValue({region_id:initial.id,rebuild_task:null,page:{...snapshot,clean_url:'/new-clean.png'}})
    renderEditor();await screen.findByTestId('current-page')
    act(()=>canvasPropsMock.mock.lastCall![0].onRegionAction([initial.id],'delete'))
    await waitFor(()=>expect(preloadImageMock).toHaveBeenCalledWith('/new-clean.png'))
    expect(screen.getByTestId('current-page')).toHaveAttribute('data-clean-url','/old-clean.png')
    expect(canvasPropsMock.mock.lastCall![0].regions).toEqual([initial])
    expect(screen.getByRole('button',{name:'矩形区域'})).toBeDisabled()
    expect(screen.getAllByRole('status',{name:'正在删除所选区域…'}).some(item=>item.classList.contains('fixed'))).toBe(true)
    await act(async()=>decoded.resolve())
    await waitFor(()=>expect(screen.getByTestId('current-page')).toHaveAttribute('data-clean-url','/new-clean.png'))
    expect(canvasPropsMock.mock.lastCall![0].regions).toEqual([])
    expect(screen.getByRole('button',{name:'矩形区域'})).toBeEnabled()
  })

  it('retains the saved geometry after a failed undo and releases the waiting dialog',async()=>{
    const snapshot=page('page-a'),initial=region({}),journal=mockDocumentHistory(snapshot,[initial])
    const update=vi.spyOn(api.regions,'update').mockImplementation(async(id,patch)=>{
      journal.commit('移动文字区域',[id],value=>{value.regions=value.regions.map(item=>({...item,...patch}))})
      return {...journal.regions()[0],editor_history:journal.state()}
    })
    renderEditor();await screen.findByTestId('current-page')
    await act(async()=>{await canvasPropsMock.mock.lastCall![0].onUpdate(initial.id,{bbox:[40,20,40,80]})})
    const gate=deferred<EditorHistoryResult>();journal.apply.mockReturnValueOnce(gate.promise)
    await userEvent.click(screen.getByRole('button',{name:'撤销'}))
    await waitFor(()=>expect(journal.apply).toHaveBeenCalledOnce())
    await act(async()=>{await canvasPropsMock.mock.lastCall![0].onUpdate(initial.id,{bbox:[80,20,40,80]})})
    await act(async()=>gate.reject(new Error('history failed')))
    await waitFor(()=>expect(screen.getByRole('button',{name:'选择 / 变换'})).toBeEnabled())
    expect(update).toHaveBeenCalledOnce()
    expect(canvasPropsMock.mock.lastCall![0].regions[0].bbox).toEqual([40,20,40,80])
    expect(screen.getByText('history failed')).toBeInTheDocument()
    expect(journal.state().undo_count).toBe(1)
  })

  it.each([1,2])('accepts fast deletion of %s regions and refreshed clean pixels without waiting for an inference task', async count => {
    const snapshot=page('page-a',{clean_url:'/old-clean.png',rendered_url:'/old-render.png',status:'TRANSLATED'})
    const first=region({}),second={...region({}),id:'region-2',region_key:'R002'}
    vi.spyOn(api.projects,'images').mockResolvedValue([snapshot])
    const get=vi.spyOn(api.images,'get').mockResolvedValue(snapshot)
    vi.mocked(api.images.regions).mockResolvedValue([first,second])
    confirmDialogMock.mockResolvedValue(true)
    const updated=page('page-a',{clean_url:'/delete-clean.png',rendered_url:null,text_layer_url:null,status:'TRANSLATED'})
    const single=vi.spyOn(api.regions,'remove').mockResolvedValue({region_id:first.id,region_keys:{[second.id]:'R001'},rebuild_task:null,page:updated})
    const bulk=vi.spyOn(api.regions,'removeMany').mockResolvedValue({region_ids:[first.id,second.id],region_keys:{},rebuild_task:null,page:updated})
    const task=vi.spyOn(api.tasks,'get')
    renderEditor();await screen.findByTestId('current-page')
    const ids=count === 1 ? [first.id] : [first.id,second.id]
    act(() => {canvasPropsMock.mock.lastCall![0].onRegionAction(ids,'delete')})
    await waitFor(() => expect(screen.getByTestId('current-page')).toHaveAttribute('data-clean-url','/delete-clean.png'))
    expect(canvasPropsMock.mock.lastCall![0].regions).toHaveLength(2-count)
    if (count === 1) {
      expect(canvasPropsMock.mock.lastCall![0].regions).toEqual([{...second,region_key:'R001'}])
      expect(propertiesPropsMock.mock.lastCall![0].regions).toEqual([{...second,region_key:'R001'}])
    }
    if (count === 1) expect(single).toHaveBeenCalledWith(first.id)
    else expect(bulk).toHaveBeenCalledWith(ids)
    expect(get).toHaveBeenCalledOnce()
    expect(task).not.toHaveBeenCalled()
  })

  it('keeps the saved scene during a slow delete and preserves it when deletion fails', async () => {
    const snapshot=page('page-a',{clean_url:'/clean.png',rendered_url:'/render.png',text_layer_url:'/text.png',status:'TRANSLATED'})
    const initial=region({})
    vi.spyOn(api.projects,'images').mockResolvedValue([snapshot])
    vi.spyOn(api.images,'get').mockResolvedValue(snapshot)
    vi.mocked(api.images.regions).mockResolvedValue([initial])
    confirmDialogMock.mockResolvedValue(true)
    const response=deferred<Awaited<ReturnType<typeof api.regions.remove>>>()
    vi.spyOn(api.regions,'remove').mockReturnValue(response.promise)
    renderEditor();await screen.findByTestId('current-page')
    act(() => useEditorStore.getState().select(initial.id))
    act(() => {canvasPropsMock.mock.lastCall![0].onRegionAction([initial.id],'delete')})
    await waitFor(() => expect(api.regions.remove).toHaveBeenCalledOnce())
    expect(canvasPropsMock.mock.lastCall![0].regions).toEqual([initial])
    expect(canvasPropsMock.mock.lastCall![0].page.rendered_url).toBe('/render.png')
    expect(screen.getAllByRole('status',{name:'正在删除所选区域…'}).some(item=>item.classList.contains('fixed'))).toBe(true)
    await act(async () => response.reject(new Error('delete failed')))
    await waitFor(() => expect(canvasPropsMock.mock.lastCall![0].regions).toHaveLength(1))
    expect(canvasPropsMock.mock.lastCall![0].page.rendered_url).toBe('/render.png')
    expect(useEditorStore.getState().selectedIds).toEqual([initial.id])
    expect(screen.getByText('delete failed')).toBeInTheDocument()
  })

  it('restores the page-30 canvas and reveals its sidebar thumbnail after a fresh load', async () => {
    const snapshots = Array.from({length:40},(_,index) => page(`page-${index+1}`,{order_index:index}))
    vi.spyOn(api.projects,'images').mockResolvedValue(snapshots)
    vi.spyOn(api.images,'get').mockResolvedValue(snapshots[29])
    vi.spyOn(HTMLElement.prototype,'clientWidth','get').mockReturnValue(200)
    vi.spyOn(HTMLElement.prototype,'clientHeight','get').mockReturnValue(500)
    const original = Object.getOwnPropertyDescriptor(HTMLElement.prototype,'scrollIntoView')
    const scroll = vi.fn()
    Object.defineProperty(HTMLElement.prototype,'scrollIntoView',{configurable:true,value:scroll})
    try {
      const first = renderEditor('/projects/project-1/editor/page-30')
      await screen.findByTestId('current-page')
      expect(screen.getByTestId('current-page')).toHaveAttribute('data-page-id','page-30')
      expect(scroll).toHaveBeenCalledOnce()
      expect(scroll.mock.contexts[0]).toBe(screen.getByRole('button',{name:/page-30.png/}))
      expect(scroll.mock.contexts[0]).toHaveAttribute('aria-current','page')
      first.unmount()
      renderEditor('/projects/project-1/editor/page-30')
      await screen.findByTestId('current-page')
      expect(scroll).toHaveBeenCalledTimes(2)
      expect(scroll.mock.contexts[1]).toBe(screen.getByRole('button',{name:/page-30.png/}))
    } finally {
      if (original) Object.defineProperty(HTMLElement.prototype,'scrollIntoView',original)
      else delete (HTMLElement.prototype as {scrollIntoView?: unknown}).scrollIntoView
    }
  })

  it('undoes creation and consecutive source/target shape edits, then redoes the same IDs and both independent frames', async () => {
    const snapshot = page('page-a',{status:'INPAINTED',clean_url:'/clean.png'})
    const initial = region({})
    const journal = mockDocumentHistory(snapshot,[initial])
    const created = {...region({manual:true}),id:'created',region_key:'R002',polygon:[[80,80],[160,80],[130,180]],bbox:[80,80,80,100],
      translated_polygon:[[80,80],[160,80],[130,180]],translated_bbox:[80,80,80,100]}
    vi.spyOn(api.regions,'create').mockImplementation(async (_pageId,payload) => {
      created.id = payload.id!
      journal.commit('创建文字区域',[created.id],value => {value.regions.push(created)})
      return created
    })
    vi.spyOn(api.regions,'update').mockImplementation(async (id,patch) => {
      journal.commit('调整文字区域',[id],value => {value.regions = value.regions.map(item => item.id === id ? {...item,...patch} : item)})
      return journal.regions().find(item => item.id === id)!
    })
    renderEditor()
    await screen.findByTestId('current-page')
    await act(async () => {await canvasPropsMock.mock.lastCall![0].onCreate(created.polygon,created.bbox)})
    const target = {translated_polygon:[[90,80],[180,110],[140,230],[50,200]],translated_bbox:[50,80,130,150],rotation:17,perspective_warp:true}
    const source = {polygon:[[85,85],[150,85],[125,160]],bbox:[85,85,65,75]}
    await act(async () => {await canvasPropsMock.mock.lastCall![0].onUpdate(created.id,target)})
    await act(async () => {await canvasPropsMock.mock.lastCall![0].onUpdate(created.id,source)})
    const final = journal.regions().find(item => item.id === created.id)!
    const displayed = () => (canvasPropsMock.mock.lastCall![0].regions as TextRegion[]).find(item => item.id === created.id)
    for (const expected of [{...created,...target},created,undefined]) {
      await waitFor(() => expect(screen.getByRole('button',{name:'撤销'})).toBeEnabled())
      await userEvent.click(screen.getByRole('button',{name:'撤销'}))
      await waitFor(() => expect(displayed()).toEqual(expected))
    }
    expect(journal.state().undo_count).toBe(0)
    for (const expected of [created,{...created,...target},final]) {
      await waitFor(() => expect(screen.getByRole('button',{name:'重做'})).toBeEnabled())
      await userEvent.click(screen.getByRole('button',{name:'重做'}))
      await waitFor(() => expect(displayed()).toEqual(expected))
    }
    expect(journal.apply).toHaveBeenCalledTimes(6)
  })

  it('flushes an in-flight shape save when Ctrl+Z is pressed, then undoes it', async () => {
    const snapshot = page('page-a',{status:'INPAINTED',clean_url:'/clean.png'})
    const initial = region({}), journal = mockDocumentHistory(snapshot,[initial])
    const saved = deferred<TextRegion>()
    let edited: TextRegion
    vi.spyOn(api.regions,'update').mockImplementation(async (_id,patch) => {
      edited = {...initial,...patch}
      return saved.promise
    })
    renderEditor()
    await screen.findByTestId('current-page')
    act(() => {void canvasPropsMock.mock.lastCall![0].onUpdate(initial.id,{polygon:[[30,30],[100,40],[80,160],[20,150]],bbox:[20,30,80,130]})})
    await waitFor(() => expect(api.regions.update).toHaveBeenCalledOnce())
    fireEvent.keyDown(window,{key:'z',code:'KeyZ',ctrlKey:true})
    expect(journal.apply).not.toHaveBeenCalled()
    expect(screen.getByRole('button',{name:'选择 / 变换'})).toBeDisabled()
    journal.commit('调整形态',[initial.id],value => {value.regions = [edited]})
    await act(async () => saved.resolve(edited))
    await waitFor(() => expect(journal.apply).toHaveBeenCalledWith(project.id,'undo',1,snapshot.id))
    expect((canvasPropsMock.mock.lastCall![0].regions as TextRegion[])[0].polygon).toEqual(initial.polygon)
  })

  it('keeps the redo branch after a failed restore and blocks duplicate commands while a restore is in flight', async () => {
    const snapshot = page('page-a',{status:'INPAINTED',clean_url:'/clean.png'})
    const initial = region({}), journal = mockDocumentHistory(snapshot,[initial])
    journal.commit('调整字号',[initial.id],value => {value.regions[0].font_size = 38})
    const original = journal.apply.getMockImplementation()!
    const pendingRestore = deferred<EditorHistoryResult>()
    journal.apply.mockImplementationOnce(() => pendingRestore.promise)
    renderEditor()
    await screen.findByTestId('current-page')
    await waitFor(() => expect(screen.getByRole('button',{name:'撤销'})).toBeEnabled())
    fireEvent.keyDown(window,{key:'z',code:'KeyZ',ctrlKey:true})
    fireEvent.keyDown(window,{key:'z',code:'KeyZ',ctrlKey:true})
    await waitFor(() => expect(journal.apply).toHaveBeenCalledOnce())
    await act(async () => pendingRestore.reject(new Error('History restore failed')))
    expect(screen.getByRole('alert')).toHaveTextContent('History restore failed')
    expect(journal.state().undo_count).toBe(1)
    journal.apply.mockImplementation(original)
    await waitFor(() => expect(screen.getByRole('button',{name:'撤销'})).toBeEnabled())
    await userEvent.click(screen.getByRole('button',{name:'撤销'}))
    await waitFor(() => expect(screen.getByRole('button',{name:'重做'})).toBeEnabled())
    await userEvent.click(screen.getByRole('button',{name:'重做'}))
    await waitFor(() => expect((canvasPropsMock.mock.lastCall![0].regions as TextRegion[])[0].font_size).toBe(38))
  })

  it('keeps project undo after a full remount and when undoing an import leaves no pages', async () => {
    const snapshot = page('page-a',{status:'INPAINTED',clean_url:'/clean.png'})
    const journal = mockDocumentHistory(snapshot,[],true)
    journal.commit('导入图片',[],value => {value.pages = [snapshot]})
    const first = renderEditor()
    await screen.findByTestId('current-page')
    await waitFor(() => expect(screen.getByRole('button',{name:'撤销'})).toBeEnabled())
    first.unmount()
    renderEditor()
    await screen.findByTestId('current-page')
    await waitFor(() => expect(screen.getByRole('button',{name:'撤销'})).toBeEnabled())
    await userEvent.click(screen.getByRole('button',{name:'撤销'}))
    await waitFor(() => expect(screen.queryByTestId('current-page')).not.toBeInTheDocument())
    await waitFor(() => expect(screen.getByRole('button',{name:'重做'})).toBeEnabled())
    await userEvent.click(screen.getByRole('button',{name:'重做'}))
    await waitFor(() => expect(screen.getByTestId('current-page')).toHaveAttribute('data-page-id',snapshot.id))
  })

  it('offers single-page commands on the blank canvas and runs them for that page', async () => {
    const snapshot = page('page-a',{status:'OCR_DONE'})
    vi.spyOn(api.projects,'images').mockResolvedValue([snapshot])
    vi.spyOn(api.images,'get').mockResolvedValue(snapshot)
    const process = vi.spyOn(api.images,'process').mockReturnValue(deferred<ProcessingTask>().promise)
    renderEditor()
    const canvas = await screen.findByTestId('current-page')
    fireEvent.contextMenu(canvas,{clientX:600,clientY:300})
    const menu = await screen.findByRole('menu',{name:'单页操作'})
    expect(within(menu).getAllByRole('menuitem').map(item => item.textContent)).toEqual(['重新检测','重新修复','重新翻译','重置当前页'])
    expect(menu).toHaveTextContent(snapshot.filename)
    await userEvent.click(within(menu).getByRole('menuitem',{name:'重新检测'}))
    await waitFor(() => expect(process).toHaveBeenCalledWith(snapshot.id,expect.objectContaining({end_stage:'ocr'})))
    expect(screen.getByRole('status',{name:/^整页处理：page-a.png/})).toBeInTheDocument()
    expect(screen.queryByRole('status',{name:/^区域处理：/})).not.toBeInTheDocument()
  })

  it('keeps single-page inference in the task list while browsing another page without overwriting it on completion', async () => {
    const first = page('page-a',{status:'OCR_DONE',clean_url:'/a-clean.png'})
    const second = page('page-b',{status:'OCR_DONE',clean_url:'/b-clean.png'})
    const secondRegions = [{...region({}),id:'b-region',image_id:second.id}]
    vi.spyOn(api.projects,'images').mockResolvedValue([first,second])
    vi.spyOn(api.images,'get').mockImplementation(async id => id === first.id ? first : second)
    vi.mocked(api.images.regions).mockImplementation(async id => id === first.id ? [region({})] : secondRegions)
    const running = maskRepairTask({id:'page-running',status:'RUNNING',progress:0,current_stage:'ocr',message:'OCR 0/1'})
    vi.spyOn(api.images,'process').mockResolvedValue(running)
    const completed = deferred<ProcessingTask>()
    vi.spyOn(api.tasks,'get').mockReturnValue(completed.promise)
    renderEditor()
    fireEvent.contextMenu(await screen.findByTestId('current-page'),{clientX:600,clientY:300})
    await userEvent.click(within(await screen.findByRole('menu',{name:'单页操作'})).getByRole('menuitem',{name:'重新检测'}))
    await screen.findByRole('listitem',{name:'page-a.png · 处理中'})
    expect(screen.getByRole('button',{name:'矩形区域'})).toBeDisabled()
    expect(screen.getByRole('button',{name:'拖拽画布'})).toBeEnabled()
    expect(screen.getByRole('button',{name:'译文'})).toBeEnabled()
    expect(document.querySelector('.fixed.cursor-wait')).toBeNull()
    await userEvent.click(screen.getByRole('button',{name:/page-b.png/}))
    await waitFor(() => expect(screen.getByTestId('current-page')).toHaveAttribute('data-page-id',second.id))
    await userEvent.click(screen.getByRole('button',{name:'对比'}))
    await act(async () => completed.resolve({...running,status:'COMPLETED',progress:1}))
    await waitFor(() => expect(screen.getByRole('button',{name:'矩形区域'})).toBeEnabled())
    expect(screen.getByTestId('current-page')).toHaveAttribute('data-page-id',second.id)
    expect(canvasPropsMock.mock.lastCall![0].regions).toEqual(secondRegions)
    expect(useEditorStore.getState().view).toBe('comparison')
    expect(screen.queryByRole('listitem',{name:'page-a.png · 已完成'})).not.toBeInTheDocument()
    expect(screen.getByText('本次操作已完成。')).toBeInTheDocument()
  })

  it('keeps a batch monitored after page navigation and reloads the page actually being read', async () => {
    showHeaderActions()
    const first = page('page-a',{region_count:1}), second = page('page-b',{region_count:1})
    vi.spyOn(api.projects,'images').mockResolvedValue([first,second])
    vi.spyOn(api.images,'get').mockImplementation(async id => id === first.id ? first : second)
    vi.mocked(api.images.regions).mockImplementation(async id => [{...region({}),image_id:id,id:`${id}-region`}])
    const tasks = [maskRepairTask({id:'batch-a',status:'RUNNING',progress:0}),maskRepairTask({id:'batch-b',image_id:second.id,status:'QUEUED',progress:0})]
    vi.spyOn(api.projects,'batch').mockResolvedValue(tasks)
    const completion = deferred<ProcessingTask[]>()
    vi.mocked(api.tasks.statuses).mockReturnValue(completion.promise)
    renderEditor(); await screen.findByTestId('current-page')
    await userEvent.click(screen.getByRole('button',{name:'批量修复并翻译'}))
    await screen.findByRole('listitem',{name:'page-b.png · 排队中'})
    await userEvent.click(screen.getByRole('img',{name:'page-b.png'}).closest('button')!)
    await waitFor(() => expect(screen.getByTestId('current-page')).toHaveAttribute('data-page-id',second.id))
    await act(async () => completion.resolve(tasks.map(task => ({...task,status:'COMPLETED',progress:1}))))
    await screen.findByText(/批量修复并翻译完成，共 2 页/)
    expect(screen.getByTestId('current-page')).toHaveAttribute('data-page-id',second.id)
    expect(canvasPropsMock.mock.lastCall![0].regions[0].image_id).toBe(second.id)
    expect(api.tasks.statuses).toHaveBeenCalledOnce()
  })

  it('captures the single region actually requested even when multiple other boxes are selected', async () => {
    const snapshot = page('page-a',{status:'OCR_DONE'})
    const first = region({}), second = {...region({}),id:'region-2',region_key:'R002'}, third = {...region({}),id:'region-3',region_key:'R003'}
    vi.spyOn(api.projects,'images').mockResolvedValue([snapshot])
    vi.spyOn(api.images,'get').mockResolvedValue(snapshot)
    vi.mocked(api.images.regions).mockResolvedValue([first,second,third])
    const task = deferred<ProcessingTask>()
    const process = vi.spyOn(api.regions,'stage').mockReturnValue(task.promise)
    renderEditor()
    await screen.findByTestId('current-page')
    act(() => useEditorStore.getState().selectMany([first.id,third.id]))
    act(() => {canvasPropsMock.mock.lastCall![0].onRegionAction([second.id],'ocr')})
    await waitFor(() => expect(process).toHaveBeenCalledWith(second.id,'ocr',{}))
    const scope = screen.getByRole('status',{name:/^区域处理：R002/})
    expect(scope).toHaveTextContent('区域处理 · 1 个')
    expect(scope).toHaveTextContent(snapshot.filename)
    expect(canvasPropsMock.mock.lastCall![0].processingRegionIds).toEqual([second.id])
    await act(async () => task.reject(new Error('OCR test failure')))
    expect(screen.queryByRole('status',{name:/^区域处理：/})).not.toBeInTheDocument()
    expect(canvasPropsMock.mock.lastCall![0].processingRegionIds).toBeUndefined()
  })

  it('reports the requested subset for a multi-region task instead of the whole page', async () => {
    const snapshot = page('page-a',{status:'OCR_DONE'})
    const first = region({}), second = {...region({}),id:'region-2',region_key:'R002'}
    vi.spyOn(api.projects,'images').mockResolvedValue([snapshot])
    vi.spyOn(api.images,'get').mockResolvedValue(snapshot)
    vi.mocked(api.images.regions).mockResolvedValue([first,second])
    const process = vi.spyOn(api.images,'process').mockReturnValue(deferred<ProcessingTask>().promise)
    renderEditor()
    await screen.findByTestId('current-page')
    act(() => useEditorStore.getState().selectMany([first.id,second.id]))
    fireEvent.keyDown(window,{key:'1',code:'Digit1',altKey:true})
    await waitFor(() => expect(process).toHaveBeenCalledWith(snapshot.id,expect.objectContaining({options:expect.objectContaining({region_ids:[first.id,second.id]})})))
    expect(screen.getByRole('status',{name:/^区域处理：R001、R002/})).toHaveTextContent('区域处理 · 2 个')
    expect(screen.queryByRole('status',{name:/^整页处理：/})).not.toBeInTheDocument()
  })

  it.each(['single','multi','page'])('shows automatic OCR within the %s translation task without starting another request', async scope => {
    const snapshot = page('page-a',{status:'OCR_DONE'})
    const selected = [{...region({}),source_text:'',translated_text:''},{...region({}),id:'region-2',region_key:'R002'}]
    const pending = deferred<ProcessingTask>()
    const running = maskRepairTask({id:'auto-ocr-translation',task_type:'translation',status:'RUNNING',current_stage:'translation',
      progress:.2,message:'翻译前自动识别 · OCR 1/2',region_id:scope === 'single' ? selected[0].id : null})
    vi.spyOn(api.projects,'images').mockResolvedValue([snapshot])
    vi.spyOn(api.images,'get').mockResolvedValue(snapshot)
    vi.mocked(api.images.regions).mockResolvedValue(selected)
    const single = vi.spyOn(api.regions,'stage').mockResolvedValue(running)
    const process = vi.spyOn(api.images,'process').mockResolvedValue(running)
    vi.spyOn(api.tasks,'get').mockReturnValue(pending.promise)
    renderEditor()
    const canvas = await screen.findByTestId('current-page')
    if (scope === 'page') {
      fireEvent.contextMenu(canvas,{clientX:600,clientY:300})
      const menu = await screen.findByRole('menu',{name:'单页操作'})
      await userEvent.click(within(menu).getByRole('menuitem',{name:'重新翻译'}))
    } else {
      const ids = (scope === 'single' ? selected.slice(0,1) : selected).map(item => item.id)
      act(() => {canvasPropsMock.mock.lastCall![0].onRegionAction(ids,'translate')})
    }
    const overlay = await screen.findByRole('status',{name:scope === 'page' ? /^整页处理：page-a.png/ : /^区域处理：R001/})
    await waitFor(() => expect(overlay).toHaveTextContent(running.message))
    expect(single).toHaveBeenCalledTimes(scope === 'single' ? 1 : 0)
    expect(process).toHaveBeenCalledTimes(scope === 'single' ? 0 : 1)
    if (scope === 'single') expect(single).toHaveBeenCalledWith(selected[0].id,'translate',{})
    else expect(process).toHaveBeenCalledWith(snapshot.id,expect.objectContaining({start_stage:'translation',end_stage:'translation'}))
    await act(async () => pending.resolve({...running,status:'COMPLETED',progress:1,message:'Completed'}))
    await waitFor(() => expect(screen.queryByRole('status',{name:/处理：/})).not.toBeInTheDocument())
  })

  it('merges only live selected regions and never forwards stale ids from removed frames',async()=>{
    const snapshot=page('page-a',{status:'COMPLETED'})
    const selected=[region({}),{...region({}),id:'region-2',region_key:'R002'}]
    vi.spyOn(api.projects,'images').mockResolvedValue([snapshot])
    vi.spyOn(api.images,'get').mockResolvedValue(snapshot)
    vi.mocked(api.images.regions).mockResolvedValue(selected)
    const merge=vi.spyOn(api.regions,'merge').mockResolvedValue({region:{...selected[0],id:'merged'},rebuild_task:null})
    renderEditor();await screen.findByTestId('current-page')
    act(()=>useEditorStore.getState().selectMany([...selected.map(item=>item.id),'deleted-region']))
    fireEvent.keyDown(window,{code:'KeyM',ctrlKey:true})
    await waitFor(()=>expect(merge).toHaveBeenCalledExactlyOnceWith(['region-1','region-2']))
    await waitFor(()=>expect(useEditorStore.getState().selectedIds).toEqual(['merged']))
  })

  it.each(['single','multi'])('does not dispatch removed commands or layout shortcuts for %s selection',async scope=>{
    const snapshot=page('page-a',{status:'COMPLETED',clean_url:'/clean.png'})
    const selected=[region({}),{...region({}),id:'region-2',region_key:'R002'}]
    vi.spyOn(api.projects,'images').mockResolvedValue([snapshot])
    vi.spyOn(api.images,'get').mockResolvedValue(snapshot)
    vi.mocked(api.images.regions).mockResolvedValue(selected)
    const process=vi.spyOn(api.images,'process')
    const stage=vi.spyOn(api.regions,'stage')
    renderEditor();await screen.findByTestId('current-page')
    const ids=(scope==='single'?selected.slice(0,1):selected).map(item=>item.id)
    act(()=>useEditorStore.getState().selectMany(ids))
    fireEvent.keyDown(window,{code:'Digit4',altKey:true})
    fireEvent.keyDown(window,{code:'Digit4',ctrlKey:true,shiftKey:true})
    for (const name of ['render','copy','split','visibility','unknown']) {
      await act(async()=>{canvasPropsMock.mock.lastCall![0].onRegionAction(ids,name)})
    }
    expect(process).not.toHaveBeenCalled()
    expect(stage).not.toHaveBeenCalled()
    expect(canvasPropsMock.mock.lastCall![0].runningAction).toBeNull()
    expect(useEditorStore.getState().selectedIds).toEqual(ids)
  })

  it.each([
    ['ocr','ocr','ocr',{crop_padding:4}],
    ['translate','translation','translation',{}],
    ['inpaint','mask','inpainting',{repair_geometry:'translated'}],
  ])('uses every Ctrl+A-selected region for the right-click %s request', async (name,start,end,options) => {
    const snapshot = page('page-a',{status:'OCR_DONE',clean_url:'/clean-a.png'})
    const selected = [region({}),{...region({}),id:'region-2',region_key:'R002'},{...region({}),id:'region-3',region_key:'R003'}]
    vi.spyOn(api.projects,'images').mockResolvedValue([snapshot])
    vi.spyOn(api.images,'get').mockResolvedValue(snapshot)
    vi.mocked(api.images.regions).mockResolvedValue(selected)
    const process = vi.spyOn(api.images,'process').mockReturnValue(deferred<ProcessingTask>().promise)
    const single = vi.spyOn(api.regions,'stage')
    renderEditor()
    await screen.findByTestId('current-page')
    act(() => useEditorStore.getState().select(selected[0].id))
    fireEvent.keyDown(window,{key:'a',code:'KeyA',ctrlKey:true})
    const ids = selected.map(item => item.id)
    expect(useEditorStore.getState().selectedIds).toEqual(ids)
    act(() => {canvasPropsMock.mock.lastCall![0].onRegionAction(useEditorStore.getState().selectedIds,name,options)})
    await waitFor(() => expect(process).toHaveBeenCalledWith(snapshot.id,{
      start_stage:start,end_stage:end,force:true,options:{...options,region_ids:ids},
    }))
    expect(single).not.toHaveBeenCalled()
    expect(screen.getByRole('status',{name:/^区域处理：R001、R002、R003/})).toHaveTextContent('区域处理 · 3 个')
  })

  it.each(['single','multi','page'])('accepts newly synchronized target geometry after %s repair instead of restoring the old merged frame', async scope => {
    const snapshot = page('page-a',{status:'INPAINTED',clean_url:'/clean-a.png'})
    const original = {...region({translation_geometry_sync:{pending:true,revision:0},ocr_geometry_stale:true}),
      bbox:[20,20,40,50],polygon:[[20,20],[60,20],[60,70],[20,70]],mask_url:'/mask.png',rotation:15,perspective_warp:true}
    const second = {...original,id:'region-2',region_key:'R002'}
    const initial = scope === 'multi' ? [original,second] : [original]
    const repaired = initial.map(item => ({...item,translated_bbox:item.bbox,translated_polygon:item.polygon,rotation:0,perspective_warp:false,
      layout_data:{...item.layout_data,translation_geometry_sync:{pending:false,revision:1,source_bbox:item.bbox,source_polygon:item.polygon}}}))
    let serverRegions = initial
    const completed: ProcessingTask = {
      id:'task-repair',project_id:project.id,image_id:snapshot.id,region_id:scope === 'single' ? original.id : null,
      task_type:'inpainting',status:'COMPLETED',progress:1,current_stage:'inpainting',message:'Repair complete',error_message:null,
      payload:{},pause_requested:false,created_at:'2026-10-13T00:00:00Z',updated_at:'2026-10-13T00:01:00Z',
    }
    vi.spyOn(api.projects,'images').mockResolvedValue([snapshot])
    vi.spyOn(api.images,'get').mockResolvedValue(snapshot)
    vi.mocked(api.images.regions).mockImplementation(async () => serverRegions)
    vi.spyOn(api.regions,'stage').mockImplementation(async () => {serverRegions = repaired; return completed})
    vi.spyOn(api.images,'process').mockImplementation(async () => {serverRegions = repaired; return completed})
    vi.spyOn(api.tasks,'get').mockResolvedValue(completed)
    renderEditor()
    await screen.findByTestId('current-page')
    act(() => useEditorStore.getState().setView('original'))
    act(() => useEditorStore.getState().selectMany(initial.map(item => item.id)))
    if (scope === 'page') {
      await openPageMenu(snapshot.filename)
      await userEvent.click(screen.getByRole('menuitem',{name:'重新修复'}))
    } else {
      act(() => {canvasPropsMock.mock.lastCall![0].onRegionAction(initial.map(item => item.id),'inpaint')})
    }
    await waitFor(() => {
      const displayed = canvasPropsMock.mock.lastCall![0].regions as TextRegion[]
      expect(displayed.map(item => item.translated_polygon)).toEqual(initial.map(item => item.polygon))
      expect(displayed.every(item => item.rotation === 0 && !item.perspective_warp)).toBe(true)
    })
    await waitFor(() => expect(screen.queryByRole('status',{name:/处理：/})).not.toBeInTheDocument())
    // A second repair with no new source edit must retain independently edited targets.
    const custom = repaired.map(item => ({...item,translated_bbox:[80,30,40,60],translated_polygon:[[80,30],[120,30],[120,90],[80,90]],rotation:-8,perspective_warp:true}))
    vi.spyOn(api.regions,'update').mockImplementation(async id => custom.find(item => item.id === id)!)
    for (const item of custom) await act(async () => {await canvasPropsMock.mock.lastCall![0].onUpdate(item.id,{translated_bbox:item.translated_bbox,translated_polygon:item.translated_polygon,rotation:item.rotation,perspective_warp:true})})
    await waitFor(() => expect(api.regions.update).toHaveBeenCalledTimes(custom.length))
    vi.mocked(api.regions.stage).mockImplementation(async () => {serverRegions = custom; return {...completed,id:'task-repeat'}})
    vi.mocked(api.images.process).mockImplementation(async () => {serverRegions = custom; return {...completed,id:'task-repeat'}})
    vi.mocked(api.tasks.get).mockResolvedValue({...completed,id:'task-repeat'})
    act(() => useEditorStore.getState().setView('original'))
    act(() => {canvasPropsMock.mock.lastCall![0].onRegionAction(initial.map(item => item.id),'inpaint')})
    await waitFor(() => expect(screen.queryByRole('status',{name:/处理：/})).not.toBeInTheDocument())
    const displayed = canvasPropsMock.mock.lastCall![0].regions as TextRegion[]
    expect(displayed.map(item => item.translated_polygon)).toEqual(custom.map(item => item.translated_polygon))
    expect(displayed.every(item => item.rotation === -8 && item.perspective_warp)).toBe(true)
  })

  it.each(['single','multi','page'])('uses the latest edited target for %s repair and accepts the synchronized source on refresh', async scope => {
    const snapshot = page('page-a',{status:'INPAINTED',clean_url:'/clean-a.png'})
    const first = region({manual:true}), second = {...region({manual:true}),id:'region-2',region_key:'R002'}
    const initial = [first,second]
    const ids = scope === 'multi' ? initial.map(item => item.id) : [first.id]
    const saveGate = deferred<void>()
    let serverRegions = initial
    const completed: ProcessingTask = {
      id:'task-reverse',project_id:project.id,image_id:snapshot.id,region_id:scope === 'single' ? first.id : null,
      task_type:'inpainting',status:'COMPLETED',progress:1,current_stage:'inpainting',message:'Repair complete',error_message:null,
      payload:{},pause_requested:false,created_at:'2026-10-13T00:00:00Z',updated_at:'2026-10-13T00:01:00Z',
    }
    vi.spyOn(api.projects,'images').mockResolvedValue([snapshot])
    vi.spyOn(api.images,'get').mockResolvedValue(snapshot)
    vi.mocked(api.images.regions).mockImplementation(async () => serverRegions)
    const update = vi.spyOn(api.regions,'update').mockImplementation(async (id, patch) => {
      await saveGate.promise
      serverRegions = serverRegions.map(item => item.id === id ? {...item,...patch} : item)
      return serverRegions.find(item => item.id === id)!
    })
    const repair = (selected: string[], options: Record<string,unknown>) => {
      expect(options.repair_geometry).toBe('translated')
      expect(update).toHaveBeenCalledTimes(ids.length)
      serverRegions = serverRegions.map(item => selected.includes(item.id) ? {...item,
        bbox:item.translated_bbox,polygon:item.translated_polygon,
        layout_data:{...item.layout_data,ocr_geometry_stale:true,translation_geometry_sync:{
          direction:'translated_to_source',pending:false,revision:1,
          previous_source_bbox:item.bbox,previous_source_polygon:item.polygon,
          source_bbox:item.translated_bbox,source_polygon:item.translated_polygon,
          translated_bbox:item.translated_bbox,translated_polygon:item.translated_polygon,
        }}} : item)
      return completed
    }
    const single = vi.spyOn(api.regions,'stage').mockImplementation(async (id,_stage,options = {}) => repair([id],options))
    const process = vi.spyOn(api.images,'process').mockImplementation(async (_id,request) => {
      const options = (request?.options || {}) as Record<string,unknown>
      return repair(scope === 'page' ? initial.map(item => item.id) : options.region_ids as string[],options)
    })
    vi.spyOn(api.tasks,'get').mockResolvedValue(completed)
    renderEditor()
    await screen.findByTestId('current-page')
    act(() => useEditorStore.getState().selectMany(ids))
    // Processing must use the latest independent target geometry.
    const target = {translated_bbox:[80,30,40,60],translated_polygon:[[80,30],[120,30],[120,90],[80,90]],rotation:-13,perspective_warp:true}
    for (const id of ids) await act(async () => {await canvasPropsMock.mock.lastCall![0].onUpdate(id,target)})
    const independent = canvasPropsMock.mock.lastCall![0].regions as TextRegion[]
    expect(independent.map(item => item.polygon)).toEqual(initial.map(item => item.polygon))
    if (scope === 'page') {
      fireEvent.contextMenu(screen.getByTestId('current-page'),{clientX:600,clientY:300})
      await userEvent.click(await screen.findByRole('menuitem',{name:'重新修复'}))
    } else {
      act(() => {canvasPropsMock.mock.lastCall![0].onRegionAction(ids,'inpaint')})
    }
    expect(single).not.toHaveBeenCalled()
    expect(process).not.toHaveBeenCalled()
    await act(async () => saveGate.resolve())
    await waitFor(() => {
      const displayed = canvasPropsMock.mock.lastCall![0].regions as TextRegion[]
      const edited = displayed.filter(item => ids.includes(item.id))
      expect(edited.every(item => JSON.stringify(item.polygon) === JSON.stringify(target.translated_polygon))).toBe(true)
      expect(edited.every(item => item.rotation === -13 && item.perspective_warp)).toBe(true)
    })
    await waitFor(() => expect(screen.queryByRole('status',{name:/处理：/})).not.toBeInTheDocument())
    const displayed = canvasPropsMock.mock.lastCall![0].regions as TextRegion[]
    expect(displayed.filter(item => ids.includes(item.id)).every(item => JSON.stringify(item.bbox) === JSON.stringify(target.translated_bbox))).toBe(true)
    expect(displayed.every(item => JSON.stringify(item.polygon) === JSON.stringify(item.translated_polygon))).toBe(true)
    if (scope === 'single') {
      expect(single).toHaveBeenCalledWith(first.id,'inpaint',{repair_geometry:'translated'})
      expect(process).not.toHaveBeenCalled()
    } else {
      expect(process).toHaveBeenCalledWith(snapshot.id,expect.objectContaining({options:expect.objectContaining({repair_geometry:'translated'})}))
      expect(single).not.toHaveBeenCalled()
    }
  })

  it.each(['original','clean','comparison'] as const)('does not reverse-sync repair from the %s view', async view => {
    const snapshot = page('page-a',{status:'INPAINTED',clean_url:'/clean-a.png'})
    vi.spyOn(api.projects,'images').mockResolvedValue([snapshot])
    vi.spyOn(api.images,'get').mockResolvedValue(snapshot)
    vi.mocked(api.images.regions).mockResolvedValue([region({})])
    const stage = vi.spyOn(api.regions,'stage').mockReturnValue(deferred<ProcessingTask>().promise)
    renderEditor()
    await screen.findByTestId('current-page')
    act(() => useEditorStore.getState().setView(view))
    act(() => {canvasPropsMock.mock.lastCall![0].onRegionAction(['region-1'],'inpaint')})
    await waitFor(() => expect(stage).toHaveBeenCalledWith('region-1','inpaint',{repair_geometry:'source'}))
  })

  it.each([
    ['ocr','ocr','ocr',{crop_padding:4}],
    ['translate','translation','translation',{}],
    ['inpaint','mask','inpainting',{repair_geometry:'translated'}],
  ])('runs right-click %s for the captured selection, not the clicked box or a later selection', async (name,start,end,options) => {
    const snapshot = page('page-a',{status:'OCR_DONE',clean_url:'/clean-a.png'})
    const first = region({}), second = {...region({}),id:'region-2',region_key:'R002',orientation:'horizontal'} as TextRegion
    const third = {...region({}),id:'region-3',region_key:'R003'}
    vi.spyOn(api.projects,'images').mockResolvedValue([snapshot])
    vi.spyOn(api.images,'get').mockResolvedValue(snapshot)
    vi.mocked(api.images.regions).mockResolvedValue([first,second,third])
    const task = deferred<ProcessingTask>()
    const process = vi.spyOn(api.images,'process').mockReturnValue(task.promise)
    const singleRegion = vi.spyOn(api.regions,'stage')
    renderEditor()
    await screen.findByTestId('current-page')
    act(() => useEditorStore.getState().selectMany([first.id,second.id]))
    // The menu captured this group before another selection became current.
    const scope = [first.id,second.id]
    act(() => useEditorStore.getState().select(third.id))
    act(() => {canvasPropsMock.mock.lastCall![0].onRegionAction(scope,name,options)})
    await waitFor(() => expect(process).toHaveBeenCalledWith(snapshot.id,{
      start_stage:start,end_stage:end,force:true,options:{...options,region_ids:scope},
    }))
    expect(process).toHaveBeenCalledOnce()
    expect(singleRegion).not.toHaveBeenCalled()
    expect(screen.getByRole('status',{name:/^区域处理：R001、R002/})).toHaveTextContent('区域处理 · 2 个')
    expect(canvasPropsMock.mock.lastCall![0].processingRegionIds).toEqual(scope)
    expect(screen.queryByRole('status',{name:/^整页处理：/})).not.toBeInTheDocument()
    await act(async () => task.reject(new Error('Multi-region test failure')))
    expect(canvasPropsMock.mock.lastCall![0].processingRegionIds).toBeUndefined()
  })

  it.each([
    ['重新检测', 'detection', 'ocr'],
    ['重新翻译', 'translation', 'translation'],
    ['重新修复', 'mask', 'rendering'],
  ])('runs %s for the right-clicked page instead of the previously selected page', async (label,start,end) => {
    const first = page('page-a'), second = page('page-b',{status:'COMPLETED',clean_url:'/clean-b.png'})
    vi.spyOn(api.projects,'images').mockResolvedValue([first,second])
    vi.spyOn(api.images,'get').mockImplementation(async id => id === first.id ? first : second)
    vi.mocked(api.images.regions).mockImplementation(async id => [{...region({manual:true}),image_id:id}])
    const process = vi.spyOn(api.images,'process').mockReturnValue(deferred<ProcessingTask>().promise)
    renderEditor()
    await screen.findByTestId('current-page')
    const toolbar = screen.getByRole('toolbar',{name:'编辑器顶部工具栏'})
    expect(within(toolbar).queryByRole('button',{name:label})).not.toBeInTheDocument()
    const menu = await openPageMenu(second.filename)
    await waitFor(() => expect(screen.getByTestId('current-page')).toHaveAttribute('data-page-id',second.id))
    expect(menu).toHaveTextContent(second.filename)
    const command = within(menu).getByRole('menuitem',{name:label})
    await waitFor(() => expect(command).toBeEnabled())
    await userEvent.click(command)
    await waitFor(() => expect(process).toHaveBeenCalledWith(second.id,expect.objectContaining({start_stage:start,end_stage:end,force:true})))
    expect(process).toHaveBeenCalledOnce()
    expect(screen.queryByRole('menu')).not.toBeInTheDocument()
  })

  it('disables menu commands until the requested page has finished loading', async () => {
    const first = page('page-a',{status:'COMPLETED'}), second = page('page-b',{status:'OCR_DONE'})
    const secondRegions = deferred<TextRegion[]>()
    vi.spyOn(api.projects,'images').mockResolvedValue([first,second])
    vi.spyOn(api.images,'get').mockImplementation(async id => id === first.id ? first : second)
    vi.mocked(api.images.regions).mockImplementation(id => id === first.id ? Promise.resolve([]) : secondRegions.promise)
    const process = vi.spyOn(api.images,'process')
    renderEditor()
    await screen.findByTestId('current-page')
    const menu = await openPageMenu(second.filename)
    for (const item of within(menu).getAllByRole('menuitem')) expect(item).toBeDisabled()
    await userEvent.click(within(menu).getByRole('menuitem',{name:'重新翻译'}))
    expect(process).not.toHaveBeenCalled()
    await act(async () => secondRegions.resolve([{...region({}),image_id:second.id}]))
    await waitFor(() => expect(within(menu).getByRole('menuitem',{name:'重新翻译'})).toBeEnabled())
    expect(within(menu).queryByRole('menuitem',{name:'重新排版'})).not.toBeInTheDocument()
    expect(screen.getByTestId('current-page')).toHaveAttribute('data-page-id',second.id)
  })

  it('keeps reset confirmation and resets only the context-menu page', async () => {
    const first = page('page-a'), second = page('page-b',{status:'OCR_DONE'})
    vi.spyOn(api.projects,'images').mockResolvedValue([first,second])
    vi.spyOn(api.images,'get').mockImplementation(async id => id === first.id ? first : second)
    const reset = vi.spyOn(api.images,'reset').mockResolvedValue({...second,status:'UPLOADED'})
    renderEditor()
    await screen.findByTestId('current-page')
    let menu = await openPageMenu(second.filename)
    await waitFor(() => expect(within(menu).getByRole('menuitem',{name:'重置当前页'})).toBeEnabled())
    confirmDialogMock.mockResolvedValueOnce(false)
    await userEvent.click(within(menu).getByRole('menuitem',{name:'重置当前页'}))
    expect(confirmDialogMock).toHaveBeenCalledWith(expect.objectContaining({tone:'danger'}))
    expect(reset).not.toHaveBeenCalled()
    menu = await openPageMenu(second.filename)
    await userEvent.click(within(menu).getByRole('menuitem',{name:'重置当前页'}))
    await waitFor(() => expect(reset).toHaveBeenCalledExactlyOnceWith(second.id))
  })

  it('saves the latest pending edit before reset so undo restores that edit rather than losing it', async () => {
    const snapshot = page('page-a',{status:'OCR_DONE'})
    const selected = region({})
    const journal = mockDocumentHistory(snapshot,[selected])
    const saved = deferred<TextRegion>()
    vi.spyOn(api.regions,'update').mockImplementation(async (_id,patch) => {
      await saved.promise
      journal.commit('编辑文字区域',[selected.id],state => {state.regions[0] = {...state.regions[0],...patch}})
      return journal.regions()[0]
    })
    const reset = vi.spyOn(api.images,'reset').mockImplementation(async () => {
      journal.commit('重置页面',[],state => {state.regions = []; state.pages[0].status = 'UPLOADED'})
      return journal.page()
    })
    renderEditor()
    await screen.findByTestId('current-page')
    act(() => {void propertiesPropsMock.mock.lastCall![0].onUpdate(selected.id,{polygon:[[10,10],[70,20],[80,100],[20,110]],bbox:[10,10,70,100]})})
    const menu = await openPageMenu(snapshot.filename)
    await userEvent.click(within(menu).getByRole('menuitem',{name:'重置当前页'}))
    expect(reset).not.toHaveBeenCalled()
    await act(async () => saved.resolve(selected))
    await waitFor(() => expect(reset).toHaveBeenCalledOnce())
    await waitFor(() => expect(screen.getByRole('button',{name:'撤销'})).toBeEnabled())
    await userEvent.click(screen.getByRole('button',{name:'撤销'}))
    await waitFor(() => expect(canvasPropsMock.mock.lastCall![0].regions[0]?.bbox).toEqual([10,10,70,100]))
    expect(confirmDialogMock).toHaveBeenCalledWith(expect.objectContaining({message:expect.stringContaining('可以通过撤销恢复')}))
  })

  it('records one history step for a keyboard nudge of multiple selected frames', async () => {
    const snapshot = page('page-a',{status:'INPAINTED',clean_url:'/clean.png'})
    const initial = [region({}),{...region({}),id:'region-2',region_key:'R002'}]
    const journal = mockDocumentHistory(snapshot,initial)
    const batch = vi.spyOn(api.regions,'updateMany').mockImplementation(async changes => {
      journal.commit('批量编辑文字区域',changes.map(change => change.id),state => {
        state.regions = state.regions.map(region => ({...region,...changes.find(change => change.id === region.id)?.patch}))
      })
      return journal.regions()
    })
    renderEditor()
    await screen.findByTestId('current-page')
    act(() => useEditorStore.getState().selectMany(initial.map(item => item.id)))
    await userEvent.keyboard('{ArrowRight}')
    await waitFor(() => expect(batch).toHaveBeenCalledOnce())
    expect(batch.mock.calls[0][0]).toHaveLength(2)
    await waitFor(() => expect(screen.getByRole('button',{name:'撤销'})).toBeEnabled())
    await userEvent.click(screen.getByRole('button',{name:'撤销'}))
    await waitFor(() => expect(canvasPropsMock.mock.lastCall![0].regions.map((item:TextRegion) => item.translated_bbox)).toEqual(initial.map(item => item.translated_bbox)))
    expect(journal.apply).toHaveBeenCalledOnce()
  })

  it('supports menu arrow keys without moving selected text geometry and restores thumbnail focus', async () => {
    const snapshot = page('page-a',{status:'OCR_DONE'})
    vi.spyOn(api.projects,'images').mockResolvedValue([snapshot])
    vi.spyOn(api.images,'get').mockResolvedValue(snapshot)
    vi.mocked(api.images.regions).mockResolvedValue([region({})])
    const update = vi.spyOn(api.regions,'update')
    renderEditor()
    await screen.findByTestId('current-page')
    act(() => useEditorStore.getState().select('region-1'))
    const menu = await openPageMenu(snapshot.filename)
    expect(within(menu).getByRole('menuitem',{name:'重新检测'})).toHaveFocus()
    await userEvent.keyboard('{ArrowDown}')
    expect(within(menu).getByRole('menuitem',{name:'重新修复'})).toHaveFocus()
    await userEvent.keyboard('{ArrowDown}{ArrowDown}')
    expect(within(menu).getByRole('menuitem',{name:'重置当前页'})).toHaveFocus()
    await userEvent.keyboard('{Escape}')
    expect(screen.queryByRole('menu')).not.toBeInTheDocument()
    expect(screen.getByRole('button',{name:new RegExp(snapshot.filename)})).toHaveFocus()
    expect(update).not.toHaveBeenCalled()
  })

  it('opens the same page menu on a touch hold and cancels it while scrolling', async () => {
    const snapshot = page('page-a')
    vi.spyOn(api.projects,'images').mockResolvedValue([snapshot])
    vi.spyOn(api.images,'get').mockResolvedValue(snapshot)
    renderEditor()
    await screen.findByTestId('current-page')
    const thumbnail = screen.getByRole('button',{name:new RegExp(snapshot.filename)})
    // JSDOM does not provide PointerEvent; supply only its input properties.
    vi.stubGlobal('PointerEvent',class extends MouseEvent {
      pointerType = 'touch'; isPrimary = true
    })
    try {
      fireEvent.pointerDown(thumbnail,{clientX:30,clientY:100})
      const menu = await screen.findByRole('menu',{name:'图片操作'})
      expect(within(menu).getAllByRole('menuitem')).toHaveLength(6)
      fireEvent.pointerUp(thumbnail); fireEvent.click(thumbnail)
      expect(menu).toBeInTheDocument()
      await userEvent.keyboard('{Escape}')
      fireEvent.pointerDown(thumbnail,{clientX:30,clientY:100})
      fireEvent.pointerMove(thumbnail,{clientX:30,clientY:140})
      await act(async () => new Promise(resolve => setTimeout(resolve,550)))
      expect(screen.queryByRole('menu')).not.toBeInTheDocument()
    } finally {vi.unstubAllGlobals()}
  })

  it('keeps the old clean image after saving a fresh mask and repairs only that region on exit', async () => {
    const currentPage = page('page-a', {clean_url: '/old-clean.png', rendered_url: '/old-rendered.png'})
    const selected = region({})
    vi.spyOn(api.projects, 'images').mockResolvedValue([currentPage])
    vi.spyOn(api.images, 'get').mockResolvedValue(currentPage)
    vi.mocked(api.images.regions).mockResolvedValue([selected])
    const saved = {...selected,mask_url:'/saved-mask.png',layout_data:{mask_generation:{method:'manual_mask',repair_pending:true}}}
    const save = vi.spyOn(api.regions, 'mask').mockResolvedValue(saved)
    const repaired = {...saved,layout_data:{mask_generation:{method:'manual_mask',repair_pending:false}}}
    const completed = maskRepairTask()
    const process = vi.spyOn(api.images,'process').mockImplementation(async () => {
      vi.mocked(api.images.get).mockResolvedValue({...currentPage,clean_url:'/new-clean.png'})
      vi.mocked(api.images.regions).mockResolvedValue([repaired])
      return completed
    })
    vi.spyOn(api.tasks,'get').mockResolvedValue(completed)
    renderEditor()
    await screen.findByTestId('current-page')
    expect(screen.queryByRole('button', {name: '蒙版橡皮擦'})).not.toBeInTheDocument()
    const fresh = {...selected, mask_url: '/fresh-mask.png', updated_at: '2026-10-12T00:00:00Z'}
    vi.mocked(api.images.regions).mockResolvedValue([fresh])
    await userEvent.click(screen.getByRole('button', {name: '蒙版'}))
    const dialog = await screen.findByRole('region', {name: 'test mask editor'})
    expect(dialog).toHaveTextContent('/fresh-mask.png')
    expect(dialog).toHaveTextContent('brush')
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    await userEvent.click(within(dialog).getByRole('button', {name: 'Save test mask'}))
    await waitFor(() => expect(dialog).toHaveTextContent('/saved-mask.png'))
    expect(save).toHaveBeenCalledWith(selected.id, expect.any(Blob), fresh.updated_at)
    expect(useEditorStore.getState().view).toBe('mask')
    expect(screen.queryByText(/蒙版已保存/)).not.toBeInTheDocument()
    expect(process).not.toHaveBeenCalled()
    await userEvent.click(screen.getByRole('button', {name: '原图'}))
    await waitFor(() => expect(screen.getByTestId('current-page')).toHaveAttribute('data-clean-url', '/new-clean.png'))
    expect(process).toHaveBeenCalledExactlyOnceWith(currentPage.id,{
      start_stage:'inpainting',end_stage:'inpainting',force:true,
      options:{region_ids:[selected.id],repair_geometry:'source'},
    })
    expect(useEditorStore.getState().view).toBe('original')
    expect(useEditorStore.getState().tool).toBe('select')
  })

  it('waits for saved strokes before repairing all changed masks together and preloads the target view before exit',async()=>{
    const snapshot=page('page-a',{clean_url:'/old-clean.png',status:'INPAINTED'})
    const changed=[region({mask_generation:{method:'manual_mask',repair_pending:true}}),
      {...region({mask_generation:{method:'manual_mask',repair_pending:true}}),id:'region-2',region_key:'R002'}]
    const untouched={...region({mask_generation:{method:'manual_mask',repair_pending:false}}),id:'region-3',region_key:'R003'}
    vi.spyOn(api.projects,'images').mockResolvedValue([snapshot]);vi.spyOn(api.images,'get').mockResolvedValue(snapshot)
    vi.mocked(api.images.regions).mockResolvedValue([...changed,untouched])
    const result=deferred<ProcessingTask>(),task=maskRepairTask()
    const process=vi.spyOn(api.images,'process').mockResolvedValue(maskRepairTask({status:'RUNNING',progress:0}))
    vi.spyOn(api.tasks,'get').mockReturnValue(result.promise)
    renderEditor();await screen.findByTestId('current-page')
    await userEvent.click(screen.getByRole('button',{name:'蒙版'}))
    const editor=await screen.findByRole('region',{name:'test mask editor'})
    const saved=deferred<void>()
    requestMaskLeaveMock.mockImplementation(leave=>{void saved.promise.then(leave)})
    await userEvent.click(screen.getByRole('button',{name:'净图'}))
    expect(process).not.toHaveBeenCalled();expect(editor).toBeInTheDocument()
    await act(async()=>saved.resolve())
    await waitFor(()=>expect(process).toHaveBeenCalledOnce())
    expect(process).toHaveBeenCalledWith(snapshot.id,{
      start_stage:'inpainting',end_stage:'inpainting',force:true,options:{region_ids:changed.map(item=>item.id),repair_geometry:'source'},
    })
    expect(screen.getByRole('status',{name:/区域处理：R001、R002.*正在修复修改后的蒙版/})).not.toHaveClass('fixed')
    expect(screen.getByRole('complementary',{name:'任务进度'})).toBeInTheDocument()
    expect(useEditorStore.getState().view).toBe('mask')
    expect(screen.getByRole('button',{name:'净图'})).toBeDisabled()
    const repaired=changed.map(item=>({...item,layout_data:{mask_generation:{method:'manual_mask',repair_pending:false}}}))
    vi.mocked(api.images.get).mockResolvedValue({...snapshot,clean_url:'/new-clean.png'})
    vi.mocked(api.images.regions).mockResolvedValue([...repaired,untouched])
    const decoded=deferred<void>()
    preloadImageMock.mockImplementation(source=>source==='/new-clean.png'?decoded.promise:Promise.resolve())
    await act(async()=>result.resolve(task))
    await waitFor(()=>expect(preloadImageMock).toHaveBeenCalledWith('/new-clean.png'))
    expect(useEditorStore.getState().view).toBe('mask')
    await act(async()=>decoded.resolve())
    await waitFor(()=>expect(screen.getByTestId('current-page')).toHaveAttribute('data-clean-url','/new-clean.png'))
    expect(useEditorStore.getState().view).toBe('clean')
    expect(canvasPropsMock.mock.lastCall![0].regions[2]).toEqual(untouched)
    await userEvent.click(screen.getByRole('button',{name:'蒙版'}))
    await screen.findByRole('region',{name:'test mask editor'})
    await userEvent.click(screen.getByRole('button',{name:'净图'}))
    expect(process).toHaveBeenCalledOnce() // No strokes or pending masks: no repeat inference.
  })

  it('does not repair after a mask undo returns to the already-repaired checkpoint',async()=>{
    const snapshot=page('page-a',{clean_url:'/clean.png',status:'INPAINTED'})
    const initial={...region({mask_generation:{method:'manual_mask',repair_pending:false}}),mask_url:'/before.png'}
    const journal=mockDocumentHistory(snapshot,[initial])
    journal.commit('编辑蒙版',[initial.id],value=>{value.regions=[{...initial,mask_url:'/after.png',layout_data:{mask_generation:{method:'manual_mask',repair_pending:true}}}]})
    vi.mocked(api.images.regions).mockImplementation(async()=>journal.regions().map(item=>({...item,editor_history:journal.state()})))
    const process=vi.spyOn(api.images,'process')
    renderEditor();await screen.findByTestId('current-page')
    await userEvent.click(screen.getByRole('button',{name:'蒙版'}))
    await screen.findByRole('region',{name:'test mask editor'})
    await userEvent.click(screen.getByRole('button',{name:'Undo cached mask'}))
    await waitFor(()=>expect(screen.getByRole('region',{name:'test mask editor'})).toHaveTextContent('/before.png'))
    await userEvent.click(screen.getByRole('button',{name:'净图'}))
    expect(screen.getByTestId('current-page')).toHaveAttribute('data-clean-url','/clean.png')
    expect(process).not.toHaveBeenCalled()
  })

  it('keeps the edited mask and old clean result on repair failure and retries when exiting again',async()=>{
    const snapshot=page('page-a',{clean_url:'/old-clean.png',status:'INPAINTED'})
    const edited={...region({mask_generation:{method:'manual_mask',repair_pending:true}}),mask_url:'/edited.png'}
    vi.spyOn(api.projects,'images').mockResolvedValue([snapshot]);vi.spyOn(api.images,'get').mockResolvedValue(snapshot)
    vi.mocked(api.images.regions).mockResolvedValue([edited])
    const process=vi.spyOn(api.images,'process').mockResolvedValue(maskRepairTask())
    vi.spyOn(api.tasks,'get').mockResolvedValueOnce(maskRepairTask({status:'FAILED',error_message:'repair failed'})).mockResolvedValue(maskRepairTask())
    renderEditor();await screen.findByTestId('current-page')
    await userEvent.click(screen.getByRole('button',{name:'蒙版'}))
    await screen.findByRole('region',{name:'test mask editor'})
    await userEvent.click(screen.getByRole('button',{name:'净图'}))
    expect(await screen.findByRole('alert')).toHaveTextContent('repair failed')
    expect(useEditorStore.getState().view).toBe('mask')
    expect(screen.getByRole('region',{name:'test mask editor'})).toHaveTextContent('/edited.png')
    expect(screen.getByRole('button',{name:'净图'})).toBeEnabled()
    vi.mocked(api.images.get).mockResolvedValue({...snapshot,clean_url:'/repaired.png'})
    vi.mocked(api.images.regions).mockResolvedValue([{...edited,layout_data:{mask_generation:{method:'manual_mask',repair_pending:false}}}])
    await userEvent.click(screen.getByRole('button',{name:'净图'}))
    await waitFor(()=>expect(screen.getByTestId('current-page')).toHaveAttribute('data-clean-url','/repaired.png'))
    expect(process).toHaveBeenCalledTimes(2)
  })

  it.each(['click','shortcut'])('finishes changed masks before navigating to another image via %s in mask mode',async method=>{
    const first=page('page-a',{clean_url:'/old-clean.png'}),second=page('page-b')
    const edited=region({mask_generation:{method:'manual_mask',repair_pending:true}}),other={...region({}),id:'other-region',image_id:second.id}
    vi.spyOn(api.projects,'images').mockResolvedValue([first,second])
    vi.spyOn(api.images,'get').mockImplementation(async id=>id===first.id?first:second)
    vi.mocked(api.images.regions).mockImplementation(async id=>id===first.id?[edited]:[other])
    const process=vi.spyOn(api.images,'process').mockResolvedValue(maskRepairTask({status:'RUNNING',progress:0}))
    const finished=deferred<ProcessingTask>();vi.spyOn(api.tasks,'get').mockReturnValue(finished.promise)
    renderEditor();await screen.findByTestId('current-page')
    await userEvent.click(screen.getByRole('button',{name:'蒙版'}))
    await screen.findByRole('region',{name:'test mask editor'})
    if (method==='shortcut') fireEvent.keyDown(window,{key:'d',code:'KeyD'})
    else await userEvent.click(screen.getByRole('button',{name:new RegExp(second.filename)}))
    await waitFor(()=>expect(process).toHaveBeenCalledOnce())
    expect(screen.getByRole('region',{name:'test mask editor'})).toHaveAttribute('data-page-id',first.id)
    await act(async()=>finished.resolve(maskRepairTask()))
    await waitFor(()=>expect(screen.getByRole('region',{name:'test mask editor'})).toHaveAttribute('data-page-id',second.id))
    expect(useEditorStore.getState().view).toBe('mask')
    expect(process.mock.calls[0][0]).toBe(first.id)
  })

  it('repairs pending masks before header navigation leaves the editor',async()=>{
    const snapshot=page('page-a',{clean_url:'/old-clean.png'}),edited=region({mask_generation:{method:'manual_mask',repair_pending:true}})
    vi.spyOn(api.projects,'images').mockResolvedValue([snapshot]);vi.spyOn(api.images,'get').mockResolvedValue(snapshot)
    vi.mocked(api.images.regions).mockResolvedValue([edited])
    const process=vi.spyOn(api.images,'process').mockResolvedValue(maskRepairTask({status:'RUNNING',progress:0}))
    const finished=deferred<ProcessingTask>();vi.spyOn(api.tasks,'get').mockReturnValue(finished.promise)
    renderEditor('/projects/project-1/editor/page-a',true);await screen.findByTestId('current-page')
    await userEvent.click(screen.getByRole('button',{name:'蒙版'}))
    await screen.findByRole('region',{name:'test mask editor'})
    await userEvent.click(screen.getByRole('link',{name:'Projects header'}))
    await waitFor(()=>expect(process).toHaveBeenCalledOnce())
    expect(screen.queryByText('Project index')).not.toBeInTheDocument()
    await act(async()=>finished.resolve(maskRepairTask()))
    await screen.findByText('Project index')
  })

  it('waits for a pending mask autosave before project undo and redoes without uploading another mask', async () => {
    const snapshot=page('page-a',{clean_url:'/clean.png',status:'INPAINTED'})
    const initial=region({}),journal=mockDocumentHistory(snapshot,[initial])
    renderEditor()
    await screen.findByTestId('current-page')
    await userEvent.click(screen.getByRole('button',{name:'蒙版'}))
    await screen.findByRole('region',{name:'test mask editor'})
    const saving=deferred<void>()
    flushMaskMock.mockReturnValueOnce(saving.promise)
    await userEvent.click(screen.getByRole('button',{name:'Begin auto mask save'}))
    await userEvent.click(screen.getByRole('button',{name:'撤销'}))
    expect(flushMaskMock).toHaveBeenCalledOnce()
    expect(journal.apply).not.toHaveBeenCalled()
    journal.commit('编辑蒙版',[initial.id],state => {state.regions[0].mask_url='/autosaved-mask.png'})
    await act(async () => saving.resolve())
    await waitFor(() => expect(journal.apply).toHaveBeenCalledOnce())
    expect(useEditorStore.getState().view).toBe('mask')
    expect(journal.regions()[0].mask_url).toBeNull()
    await waitFor(() => expect(screen.getByRole('button',{name:'重做'})).toBeEnabled())
    await userEvent.click(screen.getByRole('button',{name:'重做'}))
    await waitFor(() => expect(journal.apply).toHaveBeenCalledTimes(2))
    expect(journal.regions()[0].mask_url).toBe('/autosaved-mask.png')
    expect(screen.getByRole('region',{name:'test mask editor'})).toHaveTextContent('/autosaved-mask.png')
  })

  it('syncs a locally applied mask undo without a blocking loader, reloading images or remounting the editor', async () => {
    const snapshot=page('page-a',{clean_url:'/clean.png',status:'INPAINTED'})
    const initial={...region({}),mask_url:'/before-mask.png'}
    const journal=mockDocumentHistory(snapshot,[initial])
    journal.commit('编辑蒙版',[initial.id],value => {value.regions[0].mask_url='/after-mask.png'})
    vi.mocked(api.images.regions).mockImplementation(async () => journal.regions().map(value => ({...value,editor_history:journal.state()})))
    const original=journal.apply.getMockImplementation()!
    const saving=deferred<void>()
    journal.apply.mockImplementationOnce(async (...args) => {await saving.promise;return original(...args)})
    renderEditor();await screen.findByTestId('current-page')
    await userEvent.click(screen.getByRole('button',{name:'蒙版'}))
    const editor=await screen.findByRole('region',{name:'test mask editor'})
    preloadImageMock.mockClear();vi.mocked(prepareMaskScene).mockClear()
    await userEvent.click(within(editor).getByRole('button',{name:'Undo cached mask'}))
    await waitFor(() => expect(journal.apply).toHaveBeenCalledWith(project.id,'undo',journal.state().undo_id,snapshot.id,'cached-mask-command'))
    expect(screen.queryByText('正在撤销上一步…')).not.toBeInTheDocument()
    expect(screen.getByRole('button',{name:'蒙版画笔'})).toBeEnabled()
    expect(screen.getByRole('region',{name:'test mask editor'})).toBe(editor)
    await act(async () => saving.resolve())
    await waitFor(() => expect(editor).toHaveTextContent('/before-mask.png'))
    expect(prepareMaskScene).not.toHaveBeenCalled()
    expect(preloadImageMock).not.toHaveBeenCalled()
    expect(screen.getByRole('region',{name:'test mask editor'})).toBe(editor)
  })

  it('switches images in mask mode, including an empty page, without leaving the mode', async () => {
    const first = page('page-a'), second = page('page-b'), empty = page('page-c')
    vi.spyOn(api.projects, 'images').mockResolvedValue([first,second,empty])
    vi.spyOn(api.images, 'get').mockImplementation(async id => [first,second,empty].find(item => item.id === id)!)
    vi.mocked(api.images.regions).mockImplementation(async id => id === empty.id ? [] : [{...region({}),id:`region-${id}`,image_id:id}])
    renderEditor()
    await screen.findByTestId('current-page')
    await userEvent.click(screen.getByRole('button', {name:'蒙版'}))
    expect(await screen.findByRole('region', {name:'test mask editor'})).toHaveAttribute('data-page-id',first.id)
    await userEvent.click(screen.getByRole('button', {name:new RegExp(second.filename)}))
    await waitFor(() => expect(screen.getByRole('region', {name:'test mask editor'})).toHaveAttribute('data-page-id',second.id))
    expect(requestMaskLeaveMock).toHaveBeenCalledOnce()
    expect(useEditorStore.getState().view).toBe('mask')
    await userEvent.click(screen.getByRole('button', {name:new RegExp(empty.filename)}))
    await screen.findByText('当前页暂无文字区域，请切回原图识别或创建区域后编辑蒙版。')
    expect(useEditorStore.getState().view).toBe('mask')
    await userEvent.click(screen.getByRole('button', {name:new RegExp(first.filename)}))
    await waitFor(() => expect(screen.getByRole('region', {name:'test mask editor'})).toHaveAttribute('data-page-id',first.id))
  })
  it('keeps the current picture until all mask assets are ready and retains it on failure', async () => {
    const first = page('page-a'), second = page('page-b')
    vi.spyOn(api.projects, 'images').mockResolvedValue([first,second])
    vi.spyOn(api.images, 'get').mockImplementation(async id => id === first.id ? first : second)
    vi.mocked(api.images.regions).mockImplementation(async id => [{...region({}),id:`region-${id}`,image_id:id}])
    const opening = deferred<Awaited<ReturnType<typeof prepareMaskScene>>>()
    vi.mocked(prepareMaskScene).mockReturnValueOnce(opening.promise)
    renderEditor()
    await screen.findByTestId('current-page')
    await userEvent.click(screen.getByRole('button', {name:'蒙版'}))
    expect(screen.getByTestId('current-page')).toHaveAttribute('data-page-id',first.id)
    expect(screen.queryByText('正在读取蒙版…')).not.toBeInTheDocument()
    expect(screen.queryByRole('region', {name:'test mask editor'})).not.toBeInTheDocument()
    await act(async () => opening.resolve({} as Awaited<ReturnType<typeof prepareMaskScene>>))
    expect(await screen.findByRole('region', {name:'test mask editor'})).toHaveAttribute('data-page-id',first.id)
    const switching = deferred<Awaited<ReturnType<typeof prepareMaskScene>>>()
    vi.mocked(prepareMaskScene).mockReturnValueOnce(switching.promise)
    await userEvent.click(screen.getByRole('button', {name:new RegExp(second.filename)}))
    await waitFor(() => expect(prepareMaskScene).toHaveBeenCalledTimes(2))
    expect(screen.getByRole('region', {name:'test mask editor'})).toHaveAttribute('data-page-id',first.id)
    await act(async () => switching.reject(new Error('蒙版读取失败')))
    expect(await screen.findByRole('alert')).toHaveTextContent('蒙版读取失败')
    expect(screen.getByRole('region', {name:'test mask editor'})).toHaveAttribute('data-page-id',first.id)
    await userEvent.click(screen.getByRole('button', {name:new RegExp(second.filename)}))
    await waitFor(() => expect(screen.getByRole('region', {name:'test mask editor'})).toHaveAttribute('data-page-id',second.id))
  })

  it('routes image navigation through the unsaved-mask guard and blocks it during save', async () => {
    const first = page('page-a'), second = page('page-b')
    vi.spyOn(api.projects, 'images').mockResolvedValue([first,second])
    vi.spyOn(api.images, 'get').mockImplementation(async id => id === first.id ? first : second)
    vi.mocked(api.images.regions).mockImplementation(async id => [{...region({}),id:`region-${id}`,image_id:id}])
    renderEditor()
    await screen.findByTestId('current-page')
    await userEvent.click(screen.getByRole('button', {name:'蒙版'}))
    await screen.findByRole('region', {name:'test mask editor'})
    requestMaskLeaveMock.mockImplementationOnce(() => {})
    await userEvent.click(screen.getByRole('button', {name:new RegExp(second.filename)}))
    expect(screen.getByRole('region', {name:'test mask editor'})).toHaveAttribute('data-page-id',first.id)
    await act(async () => requestMaskLeaveMock.mock.calls[0][0]())
    await waitFor(() => expect(screen.getByRole('region', {name:'test mask editor'})).toHaveAttribute('data-page-id',second.id))
    await userEvent.click(screen.getByRole('button', {name:'Begin mask save'}))
    await userEvent.click(screen.getByRole('button', {name:new RegExp(first.filename)}))
    expect(screen.getByRole('region', {name:'test mask editor'})).toHaveAttribute('data-page-id',second.id)
    expect(requestMaskLeaveMock).toHaveBeenCalledOnce()
  })

  it('opens project context above the app shell and restores focus when dismissed', async () => {
    const currentPage = page('page-a')
    vi.spyOn(api.projects, 'images').mockResolvedValue([currentPage])
    vi.spyOn(api.images, 'get').mockResolvedValue(currentPage)
    renderEditor()
    await screen.findByTestId('current-page')
    const toolbarButton = screen.getByRole('button', {name: '原图'})
    toolbarButton.focus()
    await userEvent.keyboard('{Control>}{Shift>}c{/Shift}{/Control}')
    const dialog = screen.getByRole('dialog', {name: '翻译上下文'})
    expect(dialog.closest('.translation-context-overlay')?.parentElement).toBe(document.body)
    expect(within(dialog).getByRole('button', {name: '关闭翻译上下文'})).toHaveFocus()
    await userEvent.keyboard('{Escape}')
    expect(screen.queryByRole('dialog', {name: '翻译上下文'})).not.toBeInTheDocument()
    expect(toolbarButton).toHaveFocus()
  })

  it('saves the batch context mode without removing other project settings', async () => {
    showHeaderActions()
    const current = page('page-a')
    vi.mocked(api.projects.get).mockResolvedValue({...project, settings:{existing:'preserved'}})
    vi.spyOn(api.projects,'images').mockResolvedValue([current])
    vi.spyOn(api.images,'get').mockResolvedValue(current)
    const update = vi.spyOn(api.projects,'update').mockResolvedValue(project)
    renderEditor(); await screen.findByTestId('current-page')
    fireEvent.click(screen.getByRole('button',{name:'翻译上下文'}))
    fireEvent.click(screen.getByRole('radio',{name:'依赖前页翻译'}))
    fireEvent.change(screen.getByLabelText('项目翻译上下文 JSON'), {target:{value:'{"name":"熊"}'}})
    fireEvent.click(screen.getByRole('button',{name:'保存上下文'}))
    await waitFor(() => expect(update).toHaveBeenCalledWith(project.id, {
      translation_context:{name:'熊'},settings:{existing:'preserved',translation_context_mode:'previous_page'},
    }))
  })

  it('shows invalid context errors inside the open dialog', async () => {
    showHeaderActions()
    const current = page('page-a')
    vi.spyOn(api.projects,'images').mockResolvedValue([current])
    vi.spyOn(api.images,'get').mockResolvedValue(current)
    const update = vi.spyOn(api.projects,'update')
    renderEditor(); await screen.findByTestId('current-page')
    fireEvent.click(screen.getByRole('button',{name:'翻译上下文'}))
    fireEvent.change(screen.getByLabelText('项目翻译上下文 JSON'), {target:{value:'[]'}})
    fireEvent.click(screen.getByRole('button',{name:'保存上下文'}))
    expect(await within(screen.getByRole('dialog',{name:'翻译上下文'})).findByRole('alert')).toHaveTextContent('上下文需要是 JSON 对象')
    expect(update).not.toHaveBeenCalled()
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

  it('redetects legacy OCR regions after confirming migration', async () => {
    const snapshot = page('page-a', {status:'OCR_DONE'})
    const pendingTask = deferred<ProcessingTask>()
    vi.spyOn(api.projects, 'images').mockResolvedValue([snapshot])
    vi.spyOn(api.images, 'get').mockResolvedValue(snapshot)
    vi.mocked(api.images.regions).mockResolvedValue([region({detection:{}})])
    const process = vi.spyOn(api.images, 'process').mockReturnValue(pendingTask.promise)

    renderEditor()
    await screen.findByTestId('current-page')
    await openPageMenu(snapshot.filename)
    await userEvent.click(screen.getByRole('menuitem', {name:'重新检测'}))

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
    await openPageMenu(snapshot.filename)
    const repairButton = screen.getByRole('menuitem', {name:'重新修复'})
    expect(repairButton).toBeEnabled()
    expect(screen.queryByRole('menuitem', {name:'重新排版'})).not.toBeInTheDocument()

    await userEvent.click(repairButton)
    await waitFor(() => expect(process).toHaveBeenCalledWith(snapshot.id, {
      start_stage: 'mask',
      end_stage: 'rendering',
      force: true,
      options: {rebuild_clean: true, repair_geometry:'source'},
    }))
  })

  it.each(['UPLOADED','COMPLETED'] as const)('keeps repair and translation disabled without text boxes even at %s status', async status => {
    const snapshot = page('page-a', {status})
    vi.spyOn(api.projects, 'images').mockResolvedValue([snapshot])
    vi.spyOn(api.images, 'get').mockResolvedValue(snapshot)

    renderEditor()
    await screen.findByTestId('current-page')
    await openPageMenu(snapshot.filename)
    expect(screen.getByRole('menuitem', {name:'重新修复'})).toBeDisabled()
    expect(screen.getByRole('menuitem', {name:'重新翻译'})).toBeDisabled()
  })

  it.each(['UPLOADED','DETECTED'] as const)('enables repair and translation for an empty manual box at %s status', async status => {
    const snapshot = page('page-a', {status})
    vi.spyOn(api.projects, 'images').mockResolvedValue([snapshot])
    vi.spyOn(api.images, 'get').mockResolvedValue(snapshot)
    vi.mocked(api.images.regions).mockResolvedValue([{...region({manual:true}),source_text:'',translated_text:''}])
    const pendingTask = deferred<ProcessingTask>()
    const process = vi.spyOn(api.images, 'process').mockReturnValue(pendingTask.promise)

    renderEditor()
    await screen.findByTestId('current-page')
    await openPageMenu(snapshot.filename)
    expect(screen.getByRole('menuitem', {name:'重新修复'})).toBeEnabled()
    const translation = screen.getByRole('menuitem', {name:'重新翻译'})
    expect(translation).toBeEnabled()
    await userEvent.click(translation)
    await waitFor(() => expect(process).toHaveBeenCalledWith(snapshot.id, {
      start_stage:'translation',end_stage:'translation',force:true,options:{},
    }))
  })

  it('does not show retired grouping failures from preserved legacy metadata', async () => {
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
    await openPageMenu(snapshot.filename)
    await userEvent.click(screen.getByRole('menuitem', {name:'重新检测'}))

    const toolbar = screen.getByRole('toolbar', {name:'编辑器顶部工具栏'})
    const notice = await within(toolbar).findByRole('status')
    expect(toolbar).toHaveClass('editor-toolbar')
    expect(notice).not.toHaveClass('fixed')
    expect(notice).toHaveTextContent('重新检测与 OCR 已完成')
    expect(screen.queryByText(/气泡合并未执行/)).not.toBeInTheDocument()
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
    await openPageMenu(snapshot.filename)
    await userEvent.click(screen.getByRole('menuitem', {name:'重新检测'}))

    await waitFor(() => expect(confirmDialogMock).toHaveBeenCalledOnce())
    expect(process).not.toHaveBeenCalled()
  })
})
