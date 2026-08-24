import { useEffect, useState } from 'react'

// Full-resolution manga pages can consume several megabytes each after decode.
// Keep the active page and a small neighbor window without pinning a large
// project's worth of decoded pixels in memory.
const MAX_CACHED_IMAGES = 6
const MAX_CACHED_IMAGE_PIXELS = 16_000_000
const decodedImages = new Map<string, HTMLImageElement>()
const imageRequests = new Map<string, Promise<HTMLImageElement>>()

function rememberImage(source: string, image: HTMLImageElement) {
  decodedImages.delete(source)
  decodedImages.set(source, image)
  const decodedPixels = () => [...decodedImages.values()].reduce(
    (total, item) => total + (item.naturalWidth || item.width || 0) * (item.naturalHeight || item.height || 0),
    0,
  )
  while (decodedImages.size > MAX_CACHED_IMAGES || (decodedImages.size > 1 && decodedPixels() > MAX_CACHED_IMAGE_PIXELS)) {
    const oldest = decodedImages.keys().next().value
    if (oldest === undefined) break
    decodedImages.delete(oldest)
  }
}

function loadImageElement(source: string): Promise<HTMLImageElement> {
  const cached = decodedImages.get(source)
  if (cached) {
    rememberImage(source, cached)
    return Promise.resolve(cached)
  }
  const pending = imageRequests.get(source)
  if (pending) return pending
  const request = new Promise<HTMLImageElement>((resolve, reject) => {
    const element = new window.Image()
    element.crossOrigin = 'anonymous'
    element.onload = () => {
      const decoded = typeof element.decode === 'function' ? element.decode().catch(() => undefined) : Promise.resolve()
      void decoded.then(() => {
        rememberImage(source, element)
        resolve(element)
      })
    }
    element.onerror = () => reject(new Error(`Image load failed: ${source}`))
    element.src = source
  }).finally(() => imageRequests.delete(source))
  imageRequests.set(source, request)
  return request
}

export function preloadImage(source: string) {
  return loadImageElement(source).then(() => undefined)
}

export function useImage(source: string | null | undefined) {
  const initialImage = source ? decodedImages.get(source) || null : null
  const [loaded, setLoaded] = useState<{source: string, image: HTMLImageElement} | null>(
    initialImage && source ? {source, image: initialImage} : null,
  )
  const [loading, setLoading] = useState(Boolean(source && !initialImage))
  const [error, setError] = useState(false)
  useEffect(() => {
    if (!source) { setLoaded(null); setLoading(false); setError(false); return }
    const cached = decodedImages.get(source)
    if (cached) {
      rememberImage(source, cached)
      setLoaded({source, image: cached}); setLoading(false); setError(false); return
    }
    let active = true
    setLoaded(current => current?.source === source ? current : null)
    setLoading(true)
    setError(false)
    void loadImageElement(source).then(element => {
      if (!active) return
      setLoaded({source, image: element}); setLoading(false)
    }).catch(() => {
      if (!active) return
      setLoading(false); setError(true)
    })
    return () => { active = false }
  }, [source])
  const cached = source ? decodedImages.get(source) : null
  const image = source ? cached || (loaded?.source === source ? loaded.image : null) : null
  return {
    image,
    loading: Boolean(source && !cached && loading),
    error: Boolean(source && !cached && error),
  }
}
