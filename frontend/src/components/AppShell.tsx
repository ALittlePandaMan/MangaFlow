import { BookOpen, Boxes, Settings } from 'lucide-react'
import { createContext, useContext, useMemo, useState } from 'react'
import { NavLink, Outlet, useLocation } from 'react-router-dom'
import {cn, scrollbarClass} from '../ui'

interface AppHeaderSlots {
  editorTarget: HTMLDivElement | null
}

const AppHeaderContext = createContext<AppHeaderSlots>({editorTarget: null})

export function useAppHeaderSlots(): AppHeaderSlots {
  return useContext(AppHeaderContext)
}

export function AppShell() {
  const location = useLocation()
  const editorActive = location.pathname.includes('/editor')
  const [editorTarget, setEditorTarget] = useState<HTMLDivElement | null>(null)
  const headerSlots = useMemo(() => ({editorTarget}), [editorTarget])
  return <AppHeaderContext.Provider value={headerSlots}>
    <div className={cn('h-full pt-14', scrollbarClass)}>
      <header className="fixed inset-x-0 top-0 z-50 flex h-14 items-center border-b border-line-subtle bg-canvas px-5">
        <NavLink to="/projects" className="flex w-[260px] items-center gap-3 text-inherit no-underline" aria-label="MangaFlow 首页">
          <span className="grid size-8 place-items-center rounded-md bg-accent text-[17px] font-bold text-[#06241c]">漫</span>
          <span className="flex flex-col leading-none"><strong className="text-[16px] font-bold leading-tight tracking-[.2px]">MangaFlow</strong><small className="mt-1 font-mono text-[9px] font-medium leading-[1.4] tracking-[1.4px] text-muted">AI LETTERING STUDIO</small></span>
        </NavLink>
        <nav className="absolute left-1/2 top-0 flex h-full -translate-x-1/2 items-center gap-1">
          <NavLink to="/projects" className={({isActive}) => cn('flex h-[38px] items-center gap-2 rounded-lg px-4 text-xs font-semibold text-secondary no-underline transition-colors hover:bg-raised hover:text-ink', isActive && 'bg-raised text-ink')}><Boxes size={17} />项目</NavLink>
          <NavLink to="/settings" className={({isActive}) => cn('flex h-[38px] items-center gap-2 rounded-lg px-4 text-xs font-semibold text-secondary no-underline transition-colors hover:bg-raised hover:text-ink', isActive && 'bg-raised text-ink')}><Settings size={17} />设置</NavLink>
        </nav>
        <div className="ml-3 flex h-full min-w-0 flex-1 items-center gap-3" ref={setEditorTarget}/>
        {!editorActive && <div className="ml-auto flex items-center gap-2 font-mono text-[11px] text-muted"><span className="size-[7px] rounded-full bg-accent shadow-[0_0_8px_var(--color-accent)]" /> 本地工作区</div>}
      </header>
      <main className="h-full"><Outlet /></main>
    </div>
  </AppHeaderContext.Provider>
}

export function EmptyState({ title, detail, action }: {title: string, detail: string, action?: React.ReactNode}) {
  return <div className="flex min-h-[350px] flex-col items-center justify-center rounded-xl border border-dashed border-line bg-panel text-center text-muted"><BookOpen size={35} /><h3 className="mb-0 mt-4 text-sm text-ink">{title}</h3><p className="mb-5 mt-2 max-w-[460px] text-xs leading-5">{detail}</p>{action}</div>
}

export function Loading({ label = '正在加载…' }: {label?: string}) {
  return <div className="flex min-h-40 items-center justify-center gap-2.5 text-xs text-muted"><span className="size-[17px] animate-spin rounded-full border-2 border-line border-t-accent" />{label}</div>
}
