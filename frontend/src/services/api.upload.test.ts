import {afterEach, describe, expect, it, vi} from 'vitest'
import {api} from './api'
import {ImageUploadError} from './imageUpload'

afterEach(() => vi.unstubAllGlobals())

describe('image upload API', () => {
  it('posts bounded multipart requests with native file references, no JSON content type, and progress', async () => {
    const files = Array.from({length: 65}, (_, index) => new File(['image'], `${index}.png`, {type: 'image/png'}))
    const fetchMock = vi.fn(async (_url: string, init: RequestInit) => {
      expect(init.method).toBe('POST')
      expect(new Headers(init.headers).has('Content-Type')).toBe(false)
      const batch = (init.body as FormData).getAll('files') as File[]
      expect(batch.every(file => files.includes(file))).toBe(true)
      return new Response(JSON.stringify(batch.map(file => ({id: file.name, filename: file.name}))), {status: 201})
    })
    vi.stubGlobal('fetch', fetchMock)
    const progress = vi.fn()
    const added = await api.projects.upload('project-1', files, progress)
    expect(added.map(page => page.filename)).toEqual(files.map(file => file.name))
    expect(fetchMock).toHaveBeenCalledTimes(3)
    expect(fetchMock.mock.calls.every(([url]) => url === '/api/projects/project-1/images')).toBe(true)
    expect(progress.mock.calls.at(-1)?.[0]).toMatchObject({completedFiles: 65, totalFiles: 65, batchFiles: 0})
  })

  it('understands an nginx HTML 413 response and automatically splits the rejected request', async () => {
    const files = Array.from({length: 5}, (_, i) => new File(['image'], `${i}.png`))
    const fetchMock = vi.fn(async (_url: string, init: RequestInit) => {
      const batch = (init.body as FormData).getAll('files') as File[]
      if (batch.length > 2) return new Response('<html>413</html>', {status: 413, statusText: 'Payload Too Large'})
      return new Response(JSON.stringify(batch.map(file => ({id: file.name}))), {status: 201})
    })
    vi.stubGlobal('fetch', fetchMock)
    expect((await api.projects.upload('project-1', files)).map(page => page.id)).toEqual(files.map(file => file.name))
    expect(fetchMock.mock.calls[1][1].body).toBeInstanceOf(FormData)
  })

  it('includes a backend validation error and completed pages when a later batch fails', async () => {
    const files = Array.from({length: 33}, (_, i) => new File(['image'], `${i}.png`))
    const fetchMock = vi.fn(async (_url: string, init: RequestInit) => {
      const batch = (init.body as FormData).getAll('files') as File[]
      return new Response(JSON.stringify(batch.map(file => ({id: file.name}))), {status: 201})
    }).mockImplementationOnce(async (_url, init) => {
      const batch = (init.body as FormData).getAll('files') as File[]
      return new Response(JSON.stringify(batch.map(file => ({id: file.name}))), {status: 201})
    }).mockResolvedValueOnce(new Response(JSON.stringify({detail: 'The uploaded file is not a valid image'}), {status: 400}))
    vi.stubGlobal('fetch', fetchMock)
    const error = await api.projects.upload('project-1', files).then(
      () => {throw new Error('Expected an import failure')}, reason => reason as ImageUploadError,
    )
    expect(error).toBeInstanceOf(ImageUploadError)
    expect(error.completed).toHaveLength(32)
    expect(error.message).toContain('not a valid image')
    expect(fetchMock).toHaveBeenCalledTimes(2)
  })
})
