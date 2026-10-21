import {Braces, X} from 'lucide-react'
import {useEffect, useRef} from 'react'
import {createPortal} from 'react-dom'
import {ButtonLoading} from '../../../components/LoadingUI'
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
  {value:'snapshot', title:'提交时快照', description:'使用提交批次时已有的上下文，各页可并发翻译。'},
  {value:'previous_page', title:'依赖前页翻译', description:'等待同批次紧邻前页的新译文，优先保持内容连贯。'},
] as const

export function TranslationContextDialog({text, mode, error, saving, onTextChange, onModeChange, onError, onClose, onSave}: Props) {
  const dialogRef = useRef<HTMLFormElement>(null)
  const errorRef = useRef<HTMLParagraphElement>(null)
  useEffect(() => {
    const previous = document.activeElement instanceof HTMLElement ? document.activeElement : null
    const dialog = dialogRef.current
    const root = document.getElementById('root')
    const wasInert = root?.hasAttribute('inert')
    root?.setAttribute('inert','')
    dialog?.querySelector<HTMLButtonElement>('button')?.focus()
    const keydown = (event: KeyboardEvent) => {
      if (event.defaultPrevented) return
      if (event.key === 'Escape') {
        event.preventDefault(); event.stopPropagation()
        if (dialog?.dataset.saving !== 'true') onClose()
      }
      if (event.key !== 'Tab') return
      const controls = Array.from(dialog?.querySelectorAll<HTMLElement>('button:not(:disabled), input:checked:not(:disabled), textarea:not(:disabled), [tabindex="0"]') || [])
      const first = controls[0], last = controls.at(-1)
      if (!first || !last) {event.preventDefault(); return}
      if (event.shiftKey && (document.activeElement === first || !dialog?.contains(document.activeElement))) {
        event.preventDefault(); last.focus()
      } else if (!event.shiftKey && (document.activeElement === last || !dialog?.contains(document.activeElement))) {
        event.preventDefault(); first.focus()
      }
    }
    document.addEventListener('keydown',keydown,true)
    return () => {
      document.removeEventListener('keydown',keydown,true)
      if (!wasInert) root?.removeAttribute('inert')
      previous?.focus()
    }
  }, [onClose])
  useEffect(() => {if (error) errorRef.current?.focus()}, [error])
  const format = () => {
    try {
      const parsed: unknown = JSON.parse(text)
      if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) throw new Error('上下文需要是 JSON 对象')
      onTextChange(JSON.stringify(parsed,null,2)); onError('')
    } catch (reason) {
      onError(reason instanceof Error ? reason.message : 'JSON 格式错误')
    }
  }
  return createPortal(<div className="translation-context-overlay">
    <form ref={dialogRef} data-saving={saving} className="translation-context-dialog" role="dialog" aria-modal="true" aria-labelledby="translation-context-title" onSubmit={event => {event.preventDefault(); if (!saving) onSave()}}>
      <header className="translation-context-header"><h2 id="translation-context-title">翻译上下文</h2><button className={ghostIconButtonClass} type="button" disabled={saving} aria-label="关闭翻译上下文" onClick={onClose}><X aria-hidden="true" size={17}/></button></header>
      <div className="translation-context-body">
        <fieldset className="translation-context-modes" disabled={saving}>
          <legend>批量翻译模式</legend>
          <div className="translation-context-mode-grid">{MODES.map(item => <label className={cn('translation-context-mode',mode === item.value && 'is-selected')} key={item.value}>
            <input type="radio" name="context-mode" value={item.value} checked={mode === item.value} onChange={() => onModeChange(item.value)} aria-labelledby={'context-mode-'+item.value} aria-describedby={'context-mode-description-'+item.value}/>
            <span><span className="translation-context-mode-title"><strong id={'context-mode-'+item.value}>{item.title}</strong>{item.value === 'snapshot' && <small>默认</small>}</span><span className="translation-context-mode-description" id={'context-mode-description-'+item.value}>{item.description}</span></span>
          </label>)}</div>
          {mode === 'previous_page' && <p className="translation-context-mode-note">只等待前页翻译，不等待修复；前页不在本批次时使用快照。</p>}
        </fieldset>
        <section className="translation-context-editor">
          <div className="translation-context-editor-heading"><label htmlFor="translation-context-json">项目上下文<span>JSON</span></label><button type="button" className="translation-context-format" disabled={saving} onClick={format}><Braces aria-hidden="true" size={16}/>格式化</button></div>
          <p id="translation-context-help" className="translation-context-help">人物名、术语、称谓与章节背景。</p>
          <textarea id="translation-context-json" name="translation_context" aria-label="项目翻译上下文 JSON" aria-describedby={error ? 'translation-context-error translation-context-help' : 'translation-context-help'} aria-invalid={Boolean(error)} wrap="soft" disabled={saving} value={text} onChange={event => {onTextChange(event.target.value); if (error) onError('')}} spellCheck={false} autoComplete="off"/>
          {error && <p id="translation-context-error" ref={errorRef} className="translation-context-error" role="alert" tabIndex={-1}>{error}</p>}
        </section>
      </div>
      <footer className="translation-context-footer"><button type="button" className={buttonClass} disabled={saving} onClick={onClose}>取消</button><button type="submit" className={cn(buttonClass,'translation-context-save')} disabled={saving}>{saving ? <ButtonLoading label="保存中…"/> : '保存上下文'}</button></footer>
    </form>
  </div>,document.body)
}
