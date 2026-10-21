import {Settings} from 'lucide-react'
import {createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode, type RefObject} from 'react'
import {NavLink, useLocation} from 'react-router-dom'
import {useCompactLayout} from '../hooks/useCompactLayout'
import {cn} from '../ui'

interface SettingsNavigationState {
  open: boolean
  compact: boolean
  toggle: () => void
  close: () => void
  trigger: RefObject<HTMLButtonElement>
}

const SettingsNavigationContext = createContext<SettingsNavigationState | null>(null)

export function SettingsNavigationProvider({children}: {children: ReactNode}) {
  const compact = useCompactLayout(768)
  const {pathname} = useLocation()
  const [menuOpen, setMenuOpen] = useState(false)
  const trigger = useRef<HTMLButtonElement>(null)
  const toggle = useCallback(() => setMenuOpen(current => !current),[])
  const close = useCallback(() => setMenuOpen(false),[])
  const open = compact && pathname === '/settings' && menuOpen
  useEffect(() => {close()},[compact, pathname, close])
  const value = useMemo(() => ({open,compact,toggle,close,trigger}),[open,compact,toggle,close])
  return <SettingsNavigationContext.Provider value={value}>{children}</SettingsNavigationContext.Provider>
}

export function useSettingsNavigation() {
  return useContext(SettingsNavigationContext)
}

// Keep the existing topbar appearance; only its action changes on the compact settings page.
const navigationClass = 'flex h-10 items-center gap-2 rounded-[10px] border border-transparent px-4 text-[13px] font-semibold text-secondary no-underline outline-none transition-[background-color,border-color,color] hover:bg-hover hover:text-ink focus-visible:ring-3 focus-visible:ring-accent/20'
const activeClass = '!border-accent-line !bg-accent-soft !text-accent-soft-ink hover:!border-accent-line hover:!bg-accent-soft-hover'

export function SettingsNavigationControl() {
  const navigation = useSettingsNavigation()
  const {pathname} = useLocation()
  const content = <><Settings aria-hidden="true" size={17}/><span>设置</span></>
  if (navigation?.compact && pathname === '/settings') return <button
    ref={navigation.trigger} type="button" className={cn(navigationClass,activeClass,'cursor-pointer')}
    aria-label="设置" title="设置" aria-expanded={navigation.open} aria-controls="settings-navigation"
    onClick={navigation.toggle}>{content}</button>
  return <NavLink to="/settings" aria-label="设置" title="设置"
    className={({isActive}) => cn(navigationClass,isActive && activeClass)}>{content}</NavLink>
}
