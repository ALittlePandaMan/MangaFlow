import {RotateCcw, X} from 'lucide-react'
import {useMemo, useState} from 'react'
import type {KeyboardEvent as ReactKeyboardEvent} from 'react'
import {ButtonLoading} from './LoadingUI'
import {SelectControl} from './FormControls'
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
import {buttonClass, cn, ghostIconButtonClass} from '../ui'

interface Props {
  onCancel?: () => void
  onSaved: (shortcuts: ShortcutMap) => Promise<void>
}

export function ShortcutSettings({onCancel, onSaved}: Props) {
  const saved = useShortcutStore(state => state.shortcuts)
  const setShortcuts = useShortcutStore(state => state.setShortcuts)
  const [draft, setDraft] = useState<ShortcutMap>(() => ({...saved}))
  const [recording, setRecording] = useState<ShortcutId | null>(null)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  const [group, setGroup] = useState('tools')
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
    } finally {setSaving(false)}
  }

  const selectGroup = (id: string) => {setGroup(id); setRecording(null)}
  return <div className="settings-shortcuts">
    <div className="settings-shortcut-tabs" aria-label="快捷键分组">{SHORTCUT_GROUPS.map(item => <button key={item.id} type="button" aria-pressed={group === item.id} onClick={() => selectGroup(item.id)}>{item.label}<small>{SHORTCUT_DEFINITIONS.filter(def => def.group === item.id).length}</small></button>)}</div>
    <div className="settings-shortcut-select"><SelectControl ariaLabel="快捷键分组" value={group} options={SHORTCUT_GROUPS.map(item => ({value:item.id,label:item.label+' · '+SHORTCUT_DEFINITIONS.filter(def => def.group === item.id).length}))} onChange={selectGroup}/></div>
    {error && <p role="alert" className="settings-error">{error}</p>}
    {conflicts.size > 0 && <p role="alert" className="settings-error">存在 {conflicts.size} 组快捷键冲突，请修改后保存：{[...conflictingIds].map(id => definitionsById.get(id)?.label).join('、')}</p>}
    {SHORTCUT_DEFINITIONS.filter(item => item.group === group).map(item => {
      const conflict = conflictingIds.has(item.id)
      const conflictNames = conflict ? [...conflicts.values()].find(ids => ids.includes(item.id))?.filter(id => id !== item.id).map(id => definitionsById.get(id)?.label).join('、') : ''
      return <div className={cn('settings-shortcut-row',conflict && 'is-conflict')} key={item.id}>
        <div><strong>{item.label}</strong><small>{conflict ? '与'+conflictNames+'冲突' : item.description}</small></div>
        <div className="settings-shortcut-keys">
          <button type="button" className="settings-shortcut-key" disabled={saving} aria-label={'设置'+item.label+'快捷键'} aria-pressed={recording === item.id} title={recording === item.id ? '请按下新的快捷键，Esc 取消' : item.label+'：'+formatShortcut(draft[item.id])} onClick={() => setRecording(item.id)} onKeyDown={event => recordShortcut(event,item.id)}>{recording === item.id ? '请按键…' : formatShortcut(draft[item.id])}</button>
          <button type="button" className={ghostIconButtonClass} disabled={saving || !draft[item.id]} aria-label={'清除'+item.label+'快捷键'} onClick={() => {setRecording(null); setDraft(current => ({...current,[item.id]:''}))}}><X aria-hidden="true" size={17}/></button>
        </div>
      </div>
    })}
    <footer className="settings-actions">
      <button type="button" className={buttonClass} disabled={saving} onClick={() => {setRecording(null); setDraft({...DEFAULT_SHORTCUTS})}}><RotateCcw size={16}/>恢复默认</button>
      {onCancel && <button type="button" className={buttonClass} disabled={saving} onClick={onCancel}>取消</button>}
      <button type="button" className={cn(buttonClass,'settings-primary')} disabled={!changed || !!conflicts.size || saving} onClick={() => void save()}>{saving ? <ButtonLoading label="保存中…"/> : '保存快捷键'}</button>
    </footer>
  </div>
}
