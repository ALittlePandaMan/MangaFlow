import {MoreHorizontal} from 'lucide-react'
import {useEffect, useRef, useState, type ReactNode} from 'react'
import {useCompactLayout} from '../../../hooks/useCompactLayout'
import {buttonClass, cn} from '../../../ui'

export function EditorHeaderActions({children}: {children: ReactNode}) {
  const compact = useCompactLayout()
  const [open, setOpen] = useState(false)
  const root = useRef<HTMLDivElement>(null)
  const trigger = useRef<HTMLButtonElement>(null)
  useEffect(() => {
    if (!open || !compact) return
    const outside = (event: PointerEvent) => {
      if (event.target instanceof Node && !root.current?.contains(event.target)) setOpen(false)
    }
    const escape = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {setOpen(false); trigger.current?.focus()}
    }
    document.addEventListener('pointerdown', outside)
    document.addEventListener('keydown', escape)
    return () => {
      document.removeEventListener('pointerdown', outside)
      document.removeEventListener('keydown', escape)
    }
  }, [compact, open])
  return <div ref={root} className="editor-header-actions">
    {compact && <button ref={trigger} type="button" className={cn(buttonClass, 'app-header-icon')} aria-label="项目操作" aria-expanded={open} aria-controls="editor-project-actions" onClick={() => setOpen(!open)}><MoreHorizontal size={19}/></button>}
    <div id="editor-project-actions" className="editor-project-actions" hidden={compact && !open}>{children}</div>
  </div>
}
