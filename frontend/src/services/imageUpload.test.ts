import {describe, expect, it, vi} from 'vitest'
import type {ImagePage} from '../types'
import {ImageUploadError, uploadImagesInBatches, type ImageUploadProgress} from './imageUpload'

const MiB = 1024 * 1024
function image(index: number, bytes: number) {
  const file = new File(['test'], `${index.toString().padStart(4, '0')}.webp`, {type: 'image/webp'})
  // Test scheduling large imports without allocating hundreds of MiB of image data.
  Object.defineProperty(file, 'size', {value: bytes})
  return file
}
const pages = (files: File[]) => files.map(file => ({id: file.name, filename: file.name} as ImagePage))
const tooLarge = () => Object.assign(new Error('Payload Too Large'), {status: 413})
const expectFailure = (promise: Promise<unknown>) => promise.then(
  () => {throw new Error('Expected an import failure')},
  reason => {expect(reason).toBeInstanceOf(ImageUploadError); return reason as ImageUploadError},
)

describe('adaptive image imports', () => {
  it('imports 320 images totaling 146 MiB in bounded, sequential batches and preserves order', async () => {
    const files = Array.from({length: 320}, (_, i) => image(i, Math.floor(146 * MiB / 320)))
    let active = 0, peak = 0
    const send = vi.fn(async (batch: File[]) => {
      active++; peak = Math.max(peak, active)
      expect(batch.length).toBeLessThanOrEqual(32)
      expect(batch.reduce((sum, file) => sum + file.size + 1024 + file.name.length * 4, 4096)).toBeLessThanOrEqual(48 * MiB)
      await Promise.resolve()
      active--
      return pages(batch)
    })
    const progress = vi.fn<(value: ImageUploadProgress) => void>()
    expect(await uploadImagesInBatches(files, send, progress)).toEqual(pages(files))
    expect(send.mock.calls.flatMap(([batch]) => batch)).toEqual(files)
    expect(send.mock.calls.length).toBeGreaterThan(1)
    expect(peak).toBe(1)
    expect(progress.mock.calls.at(-1)?.[0]).toMatchObject({completedFiles: 320, totalFiles: 320, batchFiles: 0,
      completedBytes: files.reduce((sum, file) => sum + file.size, 0)})
  })

  it('also bounds the file count for thousands of tiny images', async () => {
    const files = Array.from({length: 2501}, (_, i) => image(i, 1))
    const send = vi.fn(async (batch: File[]) => pages(batch))
    const added = await uploadImagesInBatches(files, send)
    expect(added).toHaveLength(2501)
    expect(send).toHaveBeenCalledTimes(Math.ceil(2501 / 32))
    expect(send.mock.calls.every(([batch]) => batch.length <= 32)).toBe(true)
  })

  it('grows on fast requests and shrinks on slow requests without reordering files', async () => {
    let now = 0
    vi.spyOn(performance, 'now').mockImplementation(() => now)
    const files = Array.from({length: 100}, (_, i) => image(i, MiB))
    const sizes: number[] = []
    const send = vi.fn(async (batch: File[]) => {
      sizes.push(batch.length)
      now += sizes.length === 2 ? 6000 : 100
      return pages(batch)
    })
    expect(await uploadImagesInBatches(files, send)).toEqual(pages(files))
    expect(sizes[1]).toBeGreaterThan(sizes[0])
    expect(sizes[2]).toBeLessThan(sizes[1])
  })

  it('automatically retries 413 with fewer files, learns the limit, and never resends committed files', async () => {
    const files = Array.from({length: 80}, (_, i) => image(i, MiB))
    const committed: string[] = []
    const progress = vi.fn<(value: ImageUploadProgress) => void>()
    const send = vi.fn(async (batch: File[]) => {
      if (batch.reduce((sum, file) => sum + file.size, 0) > 4 * MiB) throw tooLarge()
      committed.push(...batch.map(file => file.name))
      return pages(batch)
    })
    expect(await uploadImagesInBatches(files, send, progress)).toEqual(pages(files))
    expect(send.mock.calls[1][0].length).toBeLessThan(send.mock.calls[0][0].length)
    expect(committed).toEqual(files.map(file => file.name))
    expect(new Set(committed).size).toBe(files.length)
    expect(progress.mock.calls.some(([value]) => value.resizing)).toBe(true)
  })

  it('shrinks file counts even if multipart overhead rather than image bytes causes rejection', async () => {
    const files = Array.from({length: 40}, (_, i) => image(i, 0))
    const send = vi.fn(async (batch: File[]) => {
      if (batch.length > 3) throw tooLarge()
      return pages(batch)
    })
    expect(await uploadImagesInBatches(files, send)).toEqual(pages(files))
    expect(send.mock.calls.length).toBeLessThan(40)
  })

  it('sends a single large image on its own without slicing or decoding it', async () => {
    const files = [image(0, MiB), image(1, 80 * MiB), image(2, MiB)]
    const send = vi.fn(async (batch: File[]) => pages(batch))
    expect(await uploadImagesInBatches(files, send)).toEqual(pages(files))
    expect(send.mock.calls.map(([batch]) => batch)).toEqual(files.map(file => [file]))
  })

  it('stops rather than infinitely retrying a single image that exceeds the server limit', async () => {
    const file = image(0, 120 * MiB)
    const send = vi.fn(async () => {throw tooLarge()})
    const error = await expectFailure(uploadImagesInBatches([file], send))
    expect(error).toBeInstanceOf(ImageUploadError)
    expect(error).toMatchObject({completed: [], totalFiles: 1, uncertain: false})
    expect(error.message).toContain(file.name)
    expect(error.message).toContain('单张图片')
    expect(send).toHaveBeenCalledOnce()
  })

  it.each([400, 409])('preserves completed batches and identifies the remaining range after HTTP %i', async status => {
    const files = Array.from({length: 64}, (_, i) => image(i, 1))
    const send = vi.fn(async (batch: File[]) => pages(batch))
      .mockImplementationOnce(async batch => pages(batch))
      .mockRejectedValueOnce(Object.assign(new Error('Import rejected'), {status}))
    const error = await expectFailure(uploadImagesInBatches(files, send))
    expect(error).toMatchObject({completed: pages(files.slice(0, 32)), uncertain: false})
    expect(error.message).toContain('32/64')
    expect(error.message).toContain(files[32].name)
    expect(send).toHaveBeenCalledTimes(2)
  })

  it.each([0, 408, 500, 504])('never retries an ambiguous network/server failure (%i), avoiding duplicate imports', async status => {
    const files = Array.from({length: 70}, (_, i) => image(i, 1))
    const failure = status ? Object.assign(new Error('Server error'), {status}) : new TypeError('Failed to fetch')
    const send = vi.fn(async (batch: File[]) => pages(batch)).mockRejectedValueOnce(failure)
    const error = await expectFailure(uploadImagesInBatches(files, send))
    expect(error.uncertain).toBe(true)
    expect(error.message).toContain('请先检查页面列表')
    expect(send).toHaveBeenCalledOnce()
  })

  it('rejects an incomplete response without blindly retrying a possibly committed batch', async () => {
    const files = [image(0, 10), image(1, 10)]
    const send = vi.fn(async () => pages(files.slice(0, 1)))
    const error = await expectFailure(uploadImagesInBatches(files, send))
    expect(error.uncertain).toBe(true)
    expect(error.message).toContain('数量与本批次不一致')
    expect(send).toHaveBeenCalledOnce()
  })

  it('does not send empty requests', async () => {
    const send = vi.fn(), progress = vi.fn()
    expect(await uploadImagesInBatches([], send, progress)).toEqual([])
    expect(send).not.toHaveBeenCalled()
    expect(progress).not.toHaveBeenCalled()
  })

  it('does not start more batches after the editor is left', async () => {
    const files = Array.from({length: 70}, (_, i) => image(i, 1))
    const controller = new AbortController()
    const send = vi.fn(async (batch: File[]) => {
      controller.abort()
      return pages(batch)
    })
    await expect(uploadImagesInBatches(files, send, undefined, controller.signal)).rejects.toMatchObject({name: 'AbortError'})
    expect(send).toHaveBeenCalledOnce()
  })
})
