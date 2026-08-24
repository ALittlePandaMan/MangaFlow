import {CircleAlert, Keyboard, RotateCcw, Save, X} from 'lucide-react'
import {useMemo, useState} from 'react'
import type {KeyboardEvent as ReactKeyboardEvent} from 'react'
import {ButtonLoading} from './LoadingUI'
import {
  DEFAULT_SHORTCUTS,
  formatShortcut,
  shortcutConflicts,
  shortcutFromEvent,
  SHORTCUT_DEFINITIONS,
  SHORTCUT_GROUPS,
  type ShortcutId,
  type ShortcutMap,
  useShortcutStore,
} from '../features/shortcuts/store'
import {buttonClass, cn, primaryButtonClass} from '../ui'

interface Props {
  onCancel: () => void
  onSaved: (shortcuts: ShortcutMap) => Promise<void>
}

export function ShortcutSettings({onCancel, onSaved}: Props) {
  const saved = useShortcutStore(state => state.shortcuts)
  const setShortcuts = useShortcutStore(state => state.setShortcuts)
  const [draft, setDraft] = useState<ShortcutMap>(() => ({...saved}))
  const [recording, setRecording] = useState<ShortcutId | null>(null)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  const conflicts = useMemo(() => shortcutConflicts(draft), [draft])
  const conflictingIds = useMemo(() => new Set([...conflicts.values()].flat()), [conflicts])
  const definitionsById = useMemo(() => new Map(SHORTCUT_DEFINITIONS.map(item => [item.id, item])), [])
  const changed = SHORTCUT_DEFINITIONS.some(item => draft[item.id] !== saved[item.id])

  const recordShortcut = (event: ReactKeyboardEvent<HTMLButtonElement>, id: ShortcutId) => {
    if (recording !== id) return
    event.preventDefault()
    event.stopPropagation()
    if (event.code === 'Escape') {
      setRecording(null)
      return
    }
    const binding = shortcutFromEvent(event.nativeEvent)
    if (!binding) return
    setDraft(current => ({...current, [id]:binding}))
    setRecording(null)
  }

  const save = async () => {
    if (conflicts.size || saving) return
    setSaving(true)
    setError('')
    try {
      await onSaved(draft)
      setShortcuts(draft)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason))
      setSaving(false)
    }
  }

  return <div className="flex min-h-0 flex-col">
    <div className="border-b border-line-subtle bg-panel/40 px-6 py-4">
      <div className="flex items-start gap-3 rounded-xl bg-accent/[.06] p-3 text-secondary">
        <Keyboard className="mt-0.5 shrink-0 text-accent" size={18}/>
        <div><strong className="block text-[11px] font-semibold text-ink">点击快捷键框，然后按下新的组合键</strong><p className="mb-0 mt-1 text-[9px] leading-relaxed text-muted">支持 Ctrl、Alt、Shift 与任意非修饰键组合；单独按 Esc 可取消录入。未设置的操作只能通过按钮执行。</p></div>
      </div>
    </div>
    <div className="min-h-0 flex-1 px-6 py-2">
      {SHORTCUT_GROUPS.map(group => {
        const items = SHORTCUT_DEFINITIONS.filter(item => item.group === group.id)
        return <section className="border-b border-line-subtle py-5 last:border-b-0" key={group.id}>
          <div className="mb-3 flex items-end justify-between gap-4"><div><h3 className="m-0 text-[13px] font-semibold">{group.label}</h3><p className="mb-0 mt-1 text-[9px] text-muted">{group.description}</p></div><span className="font-mono text-[9px] text-muted">{items.length} 项</span></div>
          <div className="grid grid-cols-2 gap-2 max-[720px]:grid-cols-1">
            {items.map(item => {
              const conflict = conflictingIds.has(item.id)
              const conflictNames = conflict
                ? [...conflicts.values()].find(ids => ids.includes(item.id))?.filter(id => id !== item.id).map(id => definitionsById.get(id)?.label).filter(Boolean).join('、')
                : ''
              return <div className={cn('grid min-h-[68px] grid-cols-[minmax(0,1fr)_148px] items-center gap-3 rounded-xl border bg-panel px-3 py-2 transition-colors', conflict ? 'border-danger/60 bg-danger/[.04]' : 'border-line hover:border-line-strong')} key={item.id}>
                <div className="min-w-0"><strong className="block truncate text-[10px] font-medium text-ink">{item.label}</strong><small className={cn('mt-1 block truncate text-[8px]', conflict ? 'text-danger-soft-ink' : 'text-muted')} title={conflict ? `与${conflictNames}冲突` : item.description}>{conflict ? `与${conflictNames}冲突` : item.description}</small></div>
                <div className="flex min-w-0 items-center gap-1.5">
                  <button
                    type="button"
                    className={cn('h-9 min-h-9 min-w-0 flex-1 cursor-pointer truncate rounded-lg border bg-canvas px-2 font-mono text-[9px] font-semibold text-secondary outline-none transition-colors hover:border-accent/60 hover:bg-hover hover:text-ink focus-visible:ring-2 focus-visible:ring-accent/25', recording === item.id && 'border-accent bg-accent/10 text-accent', conflict && 'border-danger/60 text-danger-soft-ink')}
                    aria-label={`设置${item.label}快捷键`}
                    aria-pressed={recording === item.id}
                    title={recording === item.id ? '请按下新的快捷键，Esc 取消' : `${item.label}：${formatShortcut(draft[item.id])}`}
                    onClick={() => setRecording(item.id)}
                    onKeyDown={event => recordShortcut(event, item.id)}
                  >{recording === item.id ? '请按键…' : formatShortcut(draft[item.id])}</button>
                  <button type="button" className="grid size-8 min-h-8 shrink-0 cursor-pointer place-items-center rounded-lg border border-line bg-transparent p-0 text-muted outline-none transition-colors hover:border-line-strong hover:bg-hover hover:text-ink focus-visible:ring-2 focus-visible:ring-accent/25 disabled:cursor-default disabled:opacity-25" disabled={!draft[item.id]} title={`清除${item.label}快捷键`} aria-label={`清除${item.label}快捷键`} onClick={() => {setRecording(null); setDraft(current => ({...current, [item.id]:''}))}}><X size={13}/></button>
                </div>
              </div>
            })}
          </div>
        </section>
      })}
    </div>
    <footer className="sticky bottom-0 flex shrink-0 items-center justify-between gap-3 border-t border-line-subtle bg-surface px-6 py-4">
      <div className="min-w-0">{error ? <span className="flex items-center gap-2 text-[9px] text-danger-soft-ink"><CircleAlert size={14}/>{error}</span> : conflicts.size ? <span className="flex items-center gap-2 text-[9px] text-danger-soft-ink"><CircleAlert size={14}/>存在 {conflicts.size} 组快捷键冲突，请修改后保存。</span> : <span className="text-[9px] text-muted">保存后会同步写入 config.yaml，并立即在当前浏览器生效。</span>}</div>
      <div className="flex shrink-0 items-center gap-2">
        <button type="button" className={buttonClass} disabled={saving} onClick={() => {setRecording(null); setDraft({...DEFAULT_SHORTCUTS})}}><RotateCcw size={14}/>恢复默认</button>
        <button type="button" className={buttonClass} disabled={saving} onClick={onCancel}>取消</button>
        <button type="button" className={primaryButtonClass} disabled={!changed || !!conflicts.size || saving} onClick={() => void save()}>{saving ? <ButtonLoading label="保存中…"/> : <><Save size={14}/>保存快捷键</>}</button>
      </div>
    </footer>
  </div>
}
