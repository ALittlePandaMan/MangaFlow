import {t, useLocale} from '../i18n'
import { BookOpen, Boxes, Check, CircleAlert, LockKeyhole, RefreshCw, Sparkles } from 'lucide-react'
import { createContext, lazy, Suspense, useContext, useEffect, useMemo, useRef, useState } from 'react'
import { NavLink, Outlet } from 'react-router-dom'
import { useGlobalDialog } from './GlobalDialog'
import { PageLoader } from './LoadingUI'
import {ThemeToggle} from './ThemeToggle'
import {SettingsNavigationControl, SettingsNavigationProvider} from './SettingsNavigation'
import {useShortcutStore} from '../features/shortcuts/store'
import { api } from '../services/api'
import type { FontResource, ModelConfiguration, ModelDescriptor, SetupStatus } from '../types'
import {buttonClass, cn, scrollbarClass} from '../ui'
import packageMetadata from '../../package.json'

const RecommendedConfigurationWizard = lazy(() => import('../pages/SettingsPage').then(module => ({default: module.RecommendedConfigurationWizard})))

interface AppHeaderSlots {
  editorTarget: HTMLDivElement | null
}

const AppHeaderContext = createContext<AppHeaderSlots>({editorTarget: null})

export function useAppHeaderSlots(): AppHeaderSlots {
  return useContext(AppHeaderContext)
}

export function AppShell() {
  useLocale()
  const [editorTarget, setEditorTarget] = useState<HTMLDivElement | null>(null)
  const [setup, setSetup] = useState<SetupStatus | null>(null)
  const [available, setAvailable] = useState<ModelDescriptor[]>([])
  const [configured, setConfigured] = useState<ModelConfiguration[]>([])
  const [fonts, setFonts] = useState<FontResource[]>([])
  const [setupError, setSetupError] = useState('')
  const [setupLoading, setSetupLoading] = useState(true)
  const headerSlots = useMemo(() => ({editorTarget}), [editorTarget])
  const loadSetup = async () => {
    setSetupLoading(true); setSetupError('')
    try {
      const [status, preferences] = await Promise.all([
        api.models.setupStatus(),
        api.preferences.get().catch(() => ({shortcuts:{}})),
      ])
      if (!status.ready) {
        const [models, fontItems] = await Promise.all([api.models.list(), api.fonts.list()])
        setAvailable(models.available); setConfigured(models.configured); setFonts(fontItems)
      }
      setSetup(status)
      if (Object.keys(preferences.shortcuts).length) useShortcutStore.getState().setShortcuts(preferences.shortcuts)
    } catch (reason) {
      setSetupError(reason instanceof Error ? reason.message : String(reason))
    } finally {
      setSetupLoading(false)
    }
  }
  useEffect(() => {void loadSetup()}, [])
  if (setupLoading) return <SetupLoadingScreen/>
  if (setupError || !setup) return <SetupFailureScreen message={setupError || t("无法读取首次启动状态")} onRetry={() => void loadSetup()}/>
  if (!setup.ready) return <RequiredSetupGate status={setup} available={available} configured={configured} fonts={fonts} onReady={setSetup}/>
  return <SettingsNavigationProvider><AppHeaderContext.Provider value={headerSlots}>
    <div className={cn('app-shell bg-app', scrollbarClass)}>
      <header className="app-header z-50 flex items-center border-b border-line-subtle bg-panel/95 backdrop-blur-xl">
        <NavLink to="/projects" className="app-brand flex shrink-0 items-center gap-3 text-inherit no-underline outline-none focus-visible:ring-3 focus-visible:ring-accent/20" title={t("MangaFlow 首页")}>
          <span className="grid size-9 place-items-center rounded-lg bg-ink text-lg font-bold text-app">漫</span>
          <span className="flex flex-col leading-none"><strong className="text-[17px] font-bold leading-tight tracking-[.2px]">MangaFlow</strong><small className="brand-subtitle mt-1 flex items-center gap-1.5 font-mono text-[9px] font-medium leading-[1.4] tracking-[1.3px] text-muted"><span>AI LETTERING STUDIO</span><span className="tracking-normal text-muted">v{packageMetadata.version}</span></small></span>
        </NavLink>
        <nav className="app-navigation flex h-full items-center gap-1">
          <NavLink to="/projects" aria-label={t("项目")} title={t("项目")} className={({isActive}) => cn('flex h-10 items-center gap-2 rounded-[10px] border border-transparent px-4 text-[14px] font-semibold text-secondary no-underline outline-none transition-[background-color,border-color,color] hover:bg-hover hover:text-ink focus-visible:ring-3 focus-visible:ring-accent/20', isActive && '!border-accent-line !bg-accent-soft !text-accent-soft-ink hover:!border-accent-line hover:!bg-accent-soft-hover')}><Boxes aria-hidden="true" size={17} /><span>{t("项目")}</span></NavLink>
          <SettingsNavigationControl/>
        </nav>
        <div className="app-header-slot ml-auto flex h-full min-w-0 items-center gap-2" ref={setEditorTarget}/>
        <ThemeToggle/>
      </header>
      <main className="min-h-0 min-w-0 bg-app"><Outlet /></main>
    </div>
  </AppHeaderContext.Provider></SettingsNavigationProvider>
}

function RequiredSetupGate({status, available, configured, fonts, onReady}: {
  status: SetupStatus
  available: ModelDescriptor[]
  configured: ModelConfiguration[]
  fonts: FontResource[]
  onReady: (status: SetupStatus) => void
}) {
  useLocale()
  const {openDialog} = useGlobalDialog()
  const opened = useRef(false)
  useEffect(() => {
    if (opened.current) return
    opened.current = true
    openDialog({
      title: status.first_run ? '首次启动配置' : '完成工作台配置',
      description:'工作台需要六项配置全部可用。系统将先检测当前设备，再安装所选模型并执行一次完整可用性检查。',
      size:'xlarge',
      dismissible:false,
      content:({close}) => <Suspense fallback={<div className="h-64"><PageLoader label={t("正在加载配置向导")}/></div>}><RecommendedConfigurationWizard
        available={available}
        configured={configured}
        fonts={fonts}
        required
        autoDetect
        onInstall={async (selections) => {
          const payload = selections
            .filter(item => item.kind !== 'font')
            .map(({kind, provider, device, config, api_key}) => ({kind, provider, device, config, api_key}))
          const installed = await api.models.bootstrap({preload:true, upgrade_fallbacks:true, selections:payload})
          const installFailures = installed.models.filter(item => item.status === 'error' || item.status === 'dependency_missing' || item.status === 'configuration_required')
          if (!installed.ok || installFailures.length) {
            throw new Error(installFailures.map(item => `${setupStageLabel(item.kind)}：${item.error || t("安装失败")}`).join('；') || t("推荐配置安装失败"))
          }
          const verified = await api.models.verifySetup()
          if (!verified.ready) {
            throw new Error(verified.stages.filter(item => item.status !== 'ready').map(item => `${setupStageLabel(item.kind)}：${item.message}`).join('；'))
          }
          close()
          onReady(verified)
        }}
      /></Suspense>,
    })
  }, [available, configured, fonts, onReady, openDialog, status.first_run])
  return <div className="relative grid h-full min-h-0 place-items-center overflow-hidden bg-app p-8">
    <div className="absolute inset-0 opacity-50 [background-image:radial-gradient(circle_at_50%_20%,var(--color-accent-soft),transparent_38%),linear-gradient(var(--workspace-grid-line)_1px,transparent_1px),linear-gradient(90deg,var(--workspace-grid-line)_1px,transparent_1px)] [background-size:auto,42px_42px,42px_42px]"/>
    <div className="relative w-[min(720px,100%)] min-w-0 max-sm:p-4 rounded-2xl bg-surface p-8 shadow-dialog"><div className="flex items-start gap-4"><span className="grid size-12 shrink-0 place-items-center rounded-xl bg-accent/15 text-accent"><LockKeyhole size={23}/></span><div><span className="font-mono text-[9px] tracking-[2px] text-accent">MANGAFLOW · FIRST RUN</span><h1 className="mb-2 mt-2 text-2xl">{t("工作台正在等待初始化")}</h1><p className="m-0 text-xs leading-relaxed text-muted">{t("完成硬件检测、模型安装和六项配置自检后，项目与编辑工作台会自动解锁。")}</p></div></div><div className="mt-6 grid grid-cols-1 min-[480px]:grid-cols-2 min-[700px]:grid-cols-3 gap-2">{status.stages.map(item => <div key={item.kind} className="flex min-h-[58px] items-center gap-2.5 rounded-lg bg-panel px-3 py-2"><span className={cn('grid size-6 shrink-0 place-items-center rounded-md', item.status === 'ready' ? 'bg-success/10 text-success' : 'bg-warning/10 text-warning')}>{item.status === 'ready' ? <Check size={13}/> : <CircleAlert size={13}/>}</span><span className="min-w-0"><strong className="block text-[9px] [overflow-wrap:anywhere]">{setupStageLabel(item.kind)}</strong><small className="mt-1 block truncate text-[8px] text-muted">{item.status === 'ready' ? t("已就绪") : item.message || t("等待配置")}</small></span></div>)}</div><div className="mt-5 flex items-center gap-2 text-[9px] text-muted"><Sparkles size={13} className="text-accent"/>{t("推荐配置窗口已自动打开，安装期间请不要关闭页面。")}</div></div>
  </div>
}

function SetupLoadingScreen() {
  useLocale()
  return <PageLoader label={t("正在检查工作台配置")} detail={t("正在验证 Provider、设备运行时与字体资源")}/>
}

function SetupFailureScreen({message, onRetry}: {message: string, onRetry: () => void}) {
  useLocale()
  return <div className="grid grid-cols-[minmax(0,1fr)] h-full min-h-0 place-items-center bg-app px-6" role="alert">
    <div className="flex min-w-0 max-w-full w-[min(480px,100%)] flex-col items-center text-center">
      <CircleAlert className="text-danger" size={30}/>
      <h1 className="mb-2 mt-4 text-lg">{t("无法检查工作台配置")}</h1>
      <p className="[overflow-wrap:anywhere] mb-5 mt-0 text-xs leading-relaxed text-muted">{message}</p>
      <button type="button" className={buttonClass} onClick={onRetry}><RefreshCw size={15}/>{t("重新检测")}</button>
    </div>
  </div>
}

function setupStageLabel(kind: string): string {
  return {detection:t("文字检测"), inpainting:t("图像修复"), ocr:t("OCR 识别"), rendering:t("排版渲染"), translation:t("云端翻译"), font:t("字体资源")}[kind] || kind
}

export function EmptyState({ title, detail, action }: {title: string, detail: string, action?: React.ReactNode}) {
  useLocale()
  return <div className="flex min-h-[350px] flex-col items-center justify-center rounded-2xl border border-dashed border-line bg-panel p-8 text-center text-muted shadow-soft"><BookOpen size={36} /><h3 className="mb-0 mt-4 text-base text-ink">{title}</h3><p className="mb-6 mt-2 max-w-[460px] text-[13px] leading-6">{detail}</p>{action}</div>
}
