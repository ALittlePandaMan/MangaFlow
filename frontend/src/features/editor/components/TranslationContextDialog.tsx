import {t, useLocale} from '../../../i18n'
import {Braces, X} from 'lucide-react'
import {useEffect, useRef} from 'react'
import {createPortal} from 'react-dom'
import {ButtonLoading} from '../../../components/LoadingUI'
import {useModalFocus} from '../../../hooks/useModalFocus'
import {buttonClass, cn, ghostIconButtonClass} from '../../../ui'
import './TranslationContextDialog.css'

type ContextMode = 'snapshot' | 'previous_page'
interface Props {
  text: string
  mode: ContextMode
  error: string
  saving: boolean
  onTextChange: (value: string) => void
  onModeChange: (value: ContextMode) => void
  onError: (message: string) => void
  onClose: () => void
  onSave: () => void
}

const MODES = [
  {value:'snapshot', get title(){return t("提交时快照")}, get description(){return t("使用提交批次时已有的上下文，各页可并发翻译。")}},
  {value:'previous_page', get title(){return t("依赖前页翻译")}, get description(){return t("等待同批次紧邻前页的新译文，优先保持内容连贯。")}},
] as const

export function TranslationContextDialog({text, mode, error, saving, onTextChange, onModeChange, onError, onClose, onSave}: Props) {
  useLocale()
  const dialogRef = useRef<HTMLFormElement>(null)
  const errorRef = useRef<HTMLParagraphElement>(null)
  useModalFocus(dialogRef, {onEscape:() => {if (!saving) onClose()}})
  useEffect(() => {if (error) errorRef.current?.focus()}, [error])
  const format = () => {
    try {
      const parsed: unknown = JSON.parse(text)
      if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) throw new Error(t("上下文需要是 JSON 对象"))
      onTextChange(JSON.stringify(parsed,null,2)); onError('')
    } catch (reason) {
      onError(reason instanceof Error ? reason.message : t("JSON 格式错误"))
    }
  }
  return createPortal(<div className="translation-context-overlay">
    <form ref={dialogRef} id="translation-context-dialog" tabIndex={-1} data-saving={saving} className="translation-context-dialog" role="dialog" aria-modal="true" aria-labelledby="translation-context-title" onSubmit={event => {event.preventDefault(); if (!saving) onSave()}}>
      <header className="translation-context-header"><h2 id="translation-context-title">{t("翻译上下文")}</h2><button className={ghostIconButtonClass} type="button" disabled={saving} aria-label={t("关闭翻译上下文")} onClick={onClose}><X aria-hidden="true" size={17}/></button></header>
      <div className="translation-context-body">
        <fieldset className="translation-context-modes" disabled={saving}>
          <legend>{t("批量翻译模式")}</legend>
          <div className="translation-context-mode-grid">{MODES.map(item => <label className={cn('translation-context-mode',mode === item.value && 'is-selected')} key={item.value}>
            <input type="radio" name="context-mode" value={item.value} checked={mode === item.value} onChange={() => onModeChange(item.value)} aria-labelledby={'context-mode-'+item.value} aria-describedby={'context-mode-description-'+item.value}/>
            <span><span className="translation-context-mode-title"><strong id={'context-mode-'+item.value}>{item.title}</strong>{item.value === 'snapshot' && <small>{t("默认")}</small>}</span><span className="translation-context-mode-description" id={'context-mode-description-'+item.value}>{item.description}</span></span>
          </label>)}</div>
          {mode === 'previous_page' && <p className="translation-context-mode-note">{t("只等待前页翻译，不等待修复；前页不在本批次时使用快照。")}</p>}
        </fieldset>
        <section className="translation-context-editor">
          <div className="translation-context-editor-heading"><label htmlFor="translation-context-json">{t("项目上下文")}<span>JSON</span></label><button type="button" className="translation-context-format" disabled={saving} onClick={format}><Braces aria-hidden="true" size={16}/>{t("格式化")}</button></div>
          <p id="translation-context-help" className="translation-context-help">{t("人物名、术语、称谓与章节背景。")}</p>
          <textarea id="translation-context-json" name="translation_context" aria-label={t("项目翻译上下文 JSON")} aria-describedby={error ? 'translation-context-error translation-context-help' : 'translation-context-help'} aria-invalid={Boolean(error)} wrap="soft" disabled={saving} value={text} onChange={event => {onTextChange(event.target.value); if (error) onError('')}} spellCheck={false} autoComplete="off"/>
          {error && <p id="translation-context-error" ref={errorRef} className="translation-context-error" role="alert" tabIndex={-1}>{error}</p>}
        </section>
      </div>
      <footer className="translation-context-footer"><button type="button" className={buttonClass} disabled={saving} onClick={onClose}>{t("取消")}</button><button type="submit" className={cn(buttonClass,'translation-context-save')} disabled={saving}>{saving ? <ButtonLoading label={t("保存中…")}/> : t("保存上下文")}</button></footer>
    </form>
  </div>,document.body)
}
