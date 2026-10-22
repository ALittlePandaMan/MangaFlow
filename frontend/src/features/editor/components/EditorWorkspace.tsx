import {t, useLocale} from '../../../i18n'
import {ChevronLeft, ChevronRight, Focus, Images, SlidersHorizontal, X} from 'lucide-react'
import {useEffect, useRef, useState, type ReactNode} from 'react'
import {useCompactLayout} from '../../../hooks/useCompactLayout'
import {cn, ghostIconButtonClass} from '../../../ui'

type Panel = 'pages' | 'properties' | null

/** One copy of each panel: resizing never creates duplicate editors or loses draft input. */
export function EditorWorkspace({pages, canvas, properties, pageId, selectedCount, onPreviousPage, onNextPage, propertiesLabel = t("属性",{context:'dock'}), propertiesTitle = t("区域属性"), canvasPromptOpen = false}: {
  pages: ReactNode, canvas: ReactNode, properties: ReactNode, pageId?: string, selectedCount: number
  onPreviousPage?: () => void, onNextPage?: () => void
  propertiesLabel?: string, propertiesTitle?: string
  canvasPromptOpen?: boolean
}) {
  useLocale()
  const compact = useCompactLayout()
  const [panel, setPanel] = useState<Panel>(null)
  const closeButton = useRef<HTMLButtonElement>(null)
  const pageButton = useRef<HTMLButtonElement>(null)
  const propertiesButton = useRef<HTMLButtonElement>(null)
  const previousPage = useRef(pageId)
  const dismiss = () => {
    setPanel(null)
    ;(panel === 'pages' ? pageButton : propertiesButton).current?.focus()
  }
  useEffect(() => {
    if (compact && panel) closeButton.current?.focus()
  }, [compact, panel])
  useEffect(() => {
    if (compact && canvasPromptOpen) setPanel(null)
  }, [compact, canvasPromptOpen])
  useEffect(() => {
    if (previousPage.current !== pageId) {
      previousPage.current = pageId
      if (compact && panel === 'pages') {
        setPanel(null)
        pageButton.current?.focus()
      }
    }
  }, [compact, pageId, panel])

  return <div className="editor-workspace" onKeyDown={event => {
    if (compact && panel && event.key === 'Escape') {event.stopPropagation(); dismiss()}
  }}>
    <div id="editor-pages" className="editor-pages" hidden={compact && panel !== 'pages'}>{pages}</div>
    <div className="editor-canvas">{canvas}</div>
    <div id="editor-properties" className="editor-properties" hidden={compact && panel !== 'properties'}>{properties}</div>
    {compact && panel && <div className="editor-sheet-heading">
      <strong>{panel === 'pages' ? t("页面列表") : propertiesTitle}</strong>
      <button ref={closeButton} type="button" className={ghostIconButtonClass} aria-label={t("收起面板")} onClick={dismiss}><X aria-hidden="true" size={17}/></button>
    </div>}
    {compact && <nav className="editor-dock" aria-label={t("工作台面板")}>
      <button className="editor-page-step" type="button" aria-label={t("上一页")} title={t("上一页")} disabled={!onPreviousPage} onClick={onPreviousPage}><ChevronLeft size={18}/><span>{t("上一页")}</span></button>
      <button ref={pageButton} type="button" aria-controls="editor-pages" aria-expanded={panel === 'pages'} className={cn(panel === 'pages' && 'is-active')} onClick={() => setPanel(panel === 'pages' ? null : 'pages')}><Images size={18}/>{t("页面")}</button>
      <button type="button" aria-label={t("返回画布")} className={cn(!panel && 'is-active')} onClick={() => setPanel(null)}><Focus size={18}/>{t("画布")}</button>
      <button ref={propertiesButton} type="button" aria-controls="editor-properties" aria-expanded={panel === 'properties'} className={cn(panel === 'properties' && 'is-active')} onClick={() => setPanel(panel === 'properties' ? null : 'properties')}><SlidersHorizontal size={18}/>{propertiesLabel}{selectedCount > 0 && <span className="editor-selection-count">{selectedCount}</span>}</button>
      <button className="editor-page-step" type="button" aria-label={t("下一页")} title={t("下一页")} disabled={!onNextPage} onClick={onNextPage}><span>{t("下一页")}</span><ChevronRight size={18}/></button>
    </nav>}
  </div>
}
