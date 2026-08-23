import { useEffect, useState } from 'react'

export function useImage(source: string | null | undefined) {
  const [image, setImage] = useState<HTMLImageElement | null>(null)
  useEffect(() => {
    if (!source) { setImage(null); return }
    const element = new window.Image()
    element.crossOrigin = 'anonymous'
    element.onload = () => setImage(element)
    element.onerror = () => setImage(null)
    element.src = `${source}${source.includes('?') ? '&' : '?'}v=${Date.now()}`
    return () => { element.onload = null; element.onerror = null }
  }, [source])
  return image
}

