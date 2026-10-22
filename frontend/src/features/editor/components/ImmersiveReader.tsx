import {t, useLocale} from '../../../i18n'
import {useEffect, useLayoutEffect, useRef, useState} from 'react'
import {createPortal} from 'react-dom'
import {Group, Image as KonvaImage, Layer, Rect, Stage} from 'react-konva'
import type {ImagePage, TextRegion} from '../../../types'
import {useImage} from '../hooks/useImage'
import {TranslatedRegions} from './MangaCanvas'

interface Props {
  page: ImagePage
  regions: TextRegion[]
  busy: boolean
  error?: string
  onPreviousPage?: () => void
  onNextPage?: () => void
  onClose: () => void
}

/** A read-only scene: same artwork/text as the editor, no editor state writes. */
export function ImmersiveReader({page, regions, busy, error, onPreviousPage, onNextPage, onClose}: Props) {
  useLocale()
  const root = useRef<HTMLDivElement>(null)
  const mounted = useRef(false)
  const fullscreenRequested = useRef(false)
  const callbacks = useRef({busy, onPreviousPage, onNextPage, onClose})
  const [viewport, setViewport] = useState(() => ({width:window.innerWidth, height:window.innerHeight}))
  const {image, loading, error:imageError} = useImage(page.clean_url || page.original_url)
  callbacks.current = {busy:busy || loading, onPreviousPage, onNextPage, onClose}
  const scale = Math.max(.001, Math.min(viewport.width/page.width, viewport.height/page.height))

  useLayoutEffect(() => {
    const node = root.current!
    const previousFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null
    const previousOverflow = document.body.style.overflow
    const background = [...document.body.children].filter((child): child is HTMLElement => child instanceof HTMLElement && child !== node)
      .map(element => ({element, inert:element.inert}))
    background.forEach(({element}) => {element.inert=true})
    document.body.style.overflow='hidden'
    node.focus({preventScroll:true})
    const resize = () => {
      const width=node.clientWidth || window.innerWidth, height=node.clientHeight || window.innerHeight
      setViewport(current => current.width === width && current.height === height ? current : {width,height})
    }
    resize()
    const observer = new ResizeObserver(resize)
    observer.observe(node)
    window.addEventListener('resize',resize)
    return () => {
      observer.disconnect()
      window.removeEventListener('resize',resize)
      document.body.style.overflow=previousOverflow
      background.forEach(({element,inert}) => {element.inert=inert})
      if (previousFocus?.isConnected) previousFocus.focus({preventScroll:true})
    }
  }, [])

  useEffect(() => {
    const node=root.current!
    mounted.current=true
    // Fullscreen may be disallowed by the browser; the viewport overlay still
    // provides the complete reading experience without requiring permission.
    let enteredFullscreen=false
    const fullscreenChange = () => {
      if (document.fullscreenElement === node) enteredFullscreen=true
      else if (enteredFullscreen) callbacks.current.onClose()
    }
    document.addEventListener('fullscreenchange',fullscreenChange)
    if (!document.fullscreenElement && node.requestFullscreen && !fullscreenRequested.current) {
      fullscreenRequested.current=true
      void node.requestFullscreen().then(() => {
        if (!mounted.current && document.fullscreenElement === node) void document.exitFullscreen().catch(() => undefined)
      }).catch(() => undefined)
    }
    return () => {
      mounted.current=false
      document.removeEventListener('fullscreenchange',fullscreenChange)
      if (document.fullscreenElement === node) void document.exitFullscreen().catch(() => undefined)
    }
  }, [])

  useEffect(() => {
    const node=root.current!
    let lastWheel=-Infinity, lastTurn=-Infinity, accumulated=0, consumed=false
    const turn = (direction:number) => {
      const current=callbacks.current
      if (current.busy) return
      if (direction > 0) current.onNextPage?.()
      else current.onPreviousPage?.()
    }
    const wheel = (event:WheelEvent) => {
      event.preventDefault()
      event.stopPropagation()
      if (event.ctrlKey || Math.abs(event.deltaY) <= Math.abs(event.deltaX)) return
      const now=performance.now()
      if (now-lastWheel > 180) {accumulated=0;consumed=false}
      lastWheel=now
      if (consumed || now-lastTurn < 300) return
      const delta=event.deltaY*(event.deltaMode === 1 ? 16 : event.deltaMode === 2 ? viewport.height : 1)
      if (Math.sign(delta) !== Math.sign(accumulated)) accumulated=0
      accumulated+=delta
      if (Math.abs(accumulated) < 40) return
      consumed=true
      lastTurn=now
      turn(Math.sign(accumulated))
    }
    const keydown = (event:KeyboardEvent) => {
      // Never let hidden editor shortcuts delete, nudge, undo or zoom a region.
      event.stopPropagation()
      if (event.isComposing || event.ctrlKey || event.metaKey || event.altKey) return
      if (event.key === 'Tab') {event.preventDefault();node.focus();return}
      if (event.key === 'Escape' || event.key === 'Enter' || event.key === ' ') {
        event.preventDefault();callbacks.current.onClose();return
      }
      const direction=({ArrowDown:1,ArrowRight:1,PageDown:1,ArrowUp:-1,ArrowLeft:-1,PageUp:-1} as Record<string,number>)[event.key]
      if (direction) {event.preventDefault();if (!event.repeat) turn(direction)}
    }
    node.addEventListener('wheel',wheel,{passive:false})
    document.addEventListener('keydown',keydown,true)
    return () => {
      node.removeEventListener('wheel',wheel)
      document.removeEventListener('keydown',keydown,true)
    }
  }, [viewport.height])

  return createPortal(<div ref={root} className="immersive-reader" role="dialog" aria-modal="true"
    aria-label={t("沉浸式阅读")} aria-describedby="immersive-reader-help" aria-busy={busy || loading} data-page-id={page.id} tabIndex={-1}
    onClick={() => onClose()} onContextMenu={event => event.preventDefault()}>
    <span id="immersive-reader-help" className="sr-only">{t("点击任意位置或按 Escape 退出，滚轮向下翻到下一页，向上翻到上一页。")}</span>
    {image && <Stage width={viewport.width} height={viewport.height} listening={false} aria-label={t("翻译后的漫画：{{v0}}", {v0: page.filename})}>
      <Layer listening={false}>
        <Group x={(viewport.width-page.width*scale)/2} y={(viewport.height-page.height*scale)/2}
          scaleX={scale} scaleY={scale} clipX={0} clipY={0} clipWidth={page.width} clipHeight={page.height}>
          <Rect width={page.width} height={page.height} fill="#ffffff"/>
          <KonvaImage image={image} width={page.width} height={page.height}/>
          <TranslatedRegions regions={regions} displayScale={scale}/>
        </Group>
      </Layer>
    </Stage>}
    {(error || imageError) ? <span className="immersive-reader-message" role="alert">{error || t("图片加载失败，请点击任意位置退出后重试。")}</span>
      : !image && <span className="immersive-reader-message" role="status">{t("正在加载漫画…")}</span>}
  </div>,document.body)
}
