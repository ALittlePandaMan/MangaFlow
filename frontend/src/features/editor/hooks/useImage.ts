import { useEffect, useState } from 'react'

const MAX_CACHED_IMAGES = 12
const decodedImages = new Map<string, HTMLImageElement>()
const imageRequests = new Map<string, Promise<HTMLImageElement>>()

function rememberImage(source: string, image: HTMLImageElement) {
  decodedImages.delete(source)
  decodedImages.set(source, image)
  while (decodedImages.size > MAX_CACHED_IMAGES) {
    const oldest = decodedImages.keys().next().value
    if (oldest === undefined) break
    decodedImages.delete(oldest)
  }
}

function loadImageElement(source: string): Promise<HTMLImageElement> {
  const cached = decodedImages.get(source)
  if (cached) return Promise.resolve(cached)
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

export function versionedImageSource(source: string, version?: string | number | null) {
  if (!version) return source
  return `${source}${source.includes('?') ? '&' : '?'}v=${encodeURIComponent(String(version))}`
}

export function preloadImage(source: string) {
  return loadImageElement(source).then(() => undefined)
}

export function useImage(source: string | null | undefined) {
  const initialImage = source ? decodedImages.get(source) || null : null
  const [image, setImage] = useState<HTMLImageElement | null>(initialImage)
  const [loading, setLoading] = useState(Boolean(source && !initialImage))
  const [error, setError] = useState(false)
  useEffect(() => {
    if (!source) { setImage(null); setLoading(false); setError(false); return }
    const cached = decodedImages.get(source)
    if (cached) { setImage(cached); setLoading(false); setError(false); return }
    let active = true
    setLoading(true)
    setError(false)
    void loadImageElement(source).then(element => {
      if (!active) return
      setImage(element); setLoading(false)
    }).catch(() => {
      if (!active) return
      setLoading(false); setError(true)
    })
    return () => { active = false }
  }, [source])
  const cached = source ? decodedImages.get(source) : null
  return {
    image: source ? cached || image : null,
    loading: Boolean(source && !cached && loading),
    error: Boolean(source && !cached && error),
  }
}
