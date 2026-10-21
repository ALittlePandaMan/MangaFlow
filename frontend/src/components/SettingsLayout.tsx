import {Blocks, ChevronDown, ChevronRight, Download} from 'lucide-react'
import {useEffect, useLayoutEffect, useRef, useState, type ReactNode} from 'react'
import {buttonClass, cn} from '../ui'
import {ButtonLoading} from './LoadingUI'
import {useSettingsNavigation} from './SettingsNavigation'
import {SETTINGS_PAGES, type SettingsPageId} from '../features/settings/navigation'
import '../pages/SettingsPage.css'


export function SettingsLayout({active = 'detection', onNavigate, onInstall, installing = false, loading = false, children}: {
  active?: SettingsPageId
  onNavigate?: (id: SettingsPageId) => void
  onInstall?: () => void
  installing?: boolean
  loading?: boolean
  children: ReactNode
}) {
  const navigation = useSettingsNavigation()
  const menuOpen = navigation?.open ?? false
  const closeMenu = navigation?.close
  const menuButton = navigation?.trigger
  const [modelsOpen, setModelsOpen] = useState(true)
  const sidebar = useRef<HTMLElement>(null)
  const main = useRef<HTMLDivElement>(null)
  const page = SETTINGS_PAGES.find(item => item.id === active)!
  useLayoutEffect(() => {
    if (main.current) main.current.scrollTop = 0
  }, [active])
  useEffect(() => {
    const target = main.current
    target?.toggleAttribute('inert', menuOpen)
    return () => {target?.removeAttribute('inert')}
  }, [menuOpen])
  useEffect(() => {
    if (!menuOpen) return
    const trigger = menuButton?.current
    const panel = sidebar.current
    panel?.querySelector<HTMLButtonElement>('[aria-current="page"]:not(:disabled)')?.focus()
    const handleKey = (event: KeyboardEvent) => {
      if (event.defaultPrevented) return
      if (event.key === 'Escape') {event.preventDefault(); closeMenu?.(); trigger?.focus()}
      if (event.key !== 'Tab') return
      const controls = [trigger, ...Array.from(panel?.querySelectorAll<HTMLButtonElement>('button:not(:disabled)') || [])].filter((item): item is HTMLButtonElement => Boolean(item && item.getClientRects().length))
      const index = controls.indexOf(document.activeElement as HTMLButtonElement)
      if (event.shiftKey && index <= 0) {event.preventDefault(); controls.at(-1)?.focus()}
      else if (!event.shiftKey && index === controls.length-1) {event.preventDefault(); controls[0]?.focus()}
    }
    document.addEventListener('keydown', handleKey)
    return () => {
      document.removeEventListener('keydown', handleKey)
      if (trigger?.isConnected && (panel?.contains(document.activeElement) || document.activeElement === document.body)) trigger.focus()
    }
  }, [menuOpen, closeMenu, menuButton])
  const navItem = (item: typeof SETTINGS_PAGES[number]) => <button type="button" key={item.id} disabled={!onNavigate} className={cn('settings-nav-item', active === item.id && 'is-active')} aria-label={SETTINGS_PAGES.slice(0,5).some(model => model.id === item.id) ? `编辑${item.label}配置` : item.label} aria-current={active === item.id ? 'page' : undefined} onClick={() => {onNavigate?.(item.id); closeMenu?.()}}><item.icon aria-hidden="true" size={18}/><span>{item.label}</span></button>
  return <section className="settings-workspace" aria-label={loading ? '正在读取设置' : '设置'} aria-busy={loading || undefined}>
    {menuOpen && <button className="settings-backdrop" type="button" aria-label="关闭设置菜单" tabIndex={-1} onClick={() => {closeMenu?.(); menuButton?.current?.focus()}}/>}
    <aside id="settings-navigation" ref={sidebar} className={cn('settings-sidebar', menuOpen && 'is-open')} aria-label="设置分类">
      <nav aria-label="设置分类导航"><button className="settings-nav-group" type="button" aria-expanded={modelsOpen} aria-controls="settings-model-navigation" onClick={() => setModelsOpen(current => !current)}><Blocks aria-hidden="true" size={18}/><span>模型与服务</span><ChevronDown aria-hidden="true" size={15} className={modelsOpen ? '' : '-rotate-90'}/></button><div id="settings-model-navigation" className="settings-model-navigation" hidden={!modelsOpen}>{SETTINGS_PAGES.slice(0,5).map(navItem)}</div>{SETTINGS_PAGES.slice(5).map(navItem)}</nav>
      <button className={cn(buttonClass, 'settings-install')} type="button" disabled={!onInstall || installing} onClick={() => {closeMenu?.(); onInstall?.()}}>{installing ? <ButtonLoading label="正在安装模型…"/> : <><Download aria-hidden="true" size={16}/>安装推荐配置</>}</button>
    </aside>
    <div className="settings-main" ref={main}><div className="settings-content"><div className="settings-breadcrumb" aria-hidden="true">设置<ChevronRight size={13}/>{page.label}</div>{children}</div></div>
  </section>
}

export function SettingsHeading({id}: {id: SettingsPageId}) {
  const page = SETTINGS_PAGES.find(item => item.id === id)!
  return <header className="settings-page-heading"><h1>{page.label}</h1><p>{page.description}</p></header>
}
