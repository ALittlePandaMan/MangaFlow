import { BookOpen, Boxes, Check, CircleAlert, LockKeyhole, RefreshCw, Settings, Sparkles } from 'lucide-react'
import { createContext, useContext, useEffect, useMemo, useRef, useState } from 'react'
import { NavLink, Outlet } from 'react-router-dom'
import { useGlobalDialog } from './GlobalDialog'
import { PageLoader } from './LoadingUI'
import {ThemeToggle} from './ThemeToggle'
import { RecommendedConfigurationWizard } from '../pages/SettingsPage'
import {useShortcutStore} from '../features/shortcuts/store'
import { api } from '../services/api'
import type { FontResource, ModelConfiguration, ModelDescriptor, SetupStatus } from '../types'
import {buttonClass, cn, scrollbarClass} from '../ui'
import packageMetadata from '../../package.json'

interface AppHeaderSlots {
  editorTarget: HTMLDivElement | null
}

const AppHeaderContext = createContext<AppHeaderSlots>({editorTarget: null})

export function useAppHeaderSlots(): AppHeaderSlots {
  return useContext(AppHeaderContext)
}

export function AppShell() {
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
      const [status, models, fontItems, preferences] = await Promise.all([
        api.models.setupStatus(),
        api.models.list(),
        api.fonts.list(),
        api.preferences.get().catch(() => ({shortcuts:{}})),
      ])
      setSetup(status); setAvailable(models.available); setConfigured(models.configured); setFonts(fontItems)
      if (Object.keys(preferences.shortcuts).length) useShortcutStore.getState().setShortcuts(preferences.shortcuts)
    } catch (reason) {
      setSetupError(reason instanceof Error ? reason.message : String(reason))
    } finally {
      setSetupLoading(false)
    }
  }
  useEffect(() => {void loadSetup()}, [])
  if (setupLoading) return <SetupLoadingScreen/>
  if (setupError || !setup) return <SetupFailureScreen message={setupError || '无法读取首次启动状态'} onRetry={() => void loadSetup()}/>
  if (!setup.ready) return <RequiredSetupGate status={setup} available={available} configured={configured} fonts={fonts} onReady={setSetup}/>
  return <AppHeaderContext.Provider value={headerSlots}>
    <div className={cn('h-full bg-app pt-16', scrollbarClass)}>
      <header className="fixed inset-x-0 top-0 z-50 flex h-16 items-center border-b border-line bg-canvas/95 px-6 shadow-[0_1px_0_rgb(255_255_255/.025)] backdrop-blur-xl">
        <NavLink to="/projects" className="flex w-[280px] items-center gap-3.5 text-inherit no-underline outline-none focus-visible:ring-3 focus-visible:ring-accent/20" aria-label="MangaFlow 首页">
          <span className="grid size-9 place-items-center rounded-lg bg-accent text-lg font-bold text-accent-ink shadow-soft">漫</span>
          <span className="flex flex-col leading-none"><strong className="text-[17px] font-bold leading-tight tracking-[.2px]">MangaFlow</strong><small className="mt-1 flex items-center gap-1.5 font-mono text-[9px] font-medium leading-[1.4] tracking-[1.3px] text-muted"><span>AI LETTERING STUDIO</span><span className="tracking-normal text-accent/90">v{packageMetadata.version}</span></small></span>
        </NavLink>
        <nav className="absolute left-1/2 top-0 flex h-full -translate-x-1/2 items-center gap-1">
          <NavLink to="/projects" className={({isActive}) => cn('flex h-10 items-center gap-2 rounded-[10px] border border-transparent px-4 text-[13px] font-semibold text-secondary no-underline outline-none transition-[background-color,border-color,color] hover:border-accent/20 hover:bg-accent/[.07] hover:text-accent focus-visible:ring-3 focus-visible:ring-accent/20', isActive && '!border-accent/25 !bg-accent/15 !text-accent hover:!border-accent/35 hover:!bg-accent/20')}><Boxes aria-hidden="true" size={17} />项目</NavLink>
          <NavLink to="/settings" className={({isActive}) => cn('flex h-10 items-center gap-2 rounded-[10px] border border-transparent px-4 text-[13px] font-semibold text-secondary no-underline outline-none transition-[background-color,border-color,color] hover:border-accent/20 hover:bg-accent/[.07] hover:text-accent focus-visible:ring-3 focus-visible:ring-accent/20', isActive && '!border-accent/25 !bg-accent/15 !text-accent hover:!border-accent/35 hover:!bg-accent/20')}><Settings aria-hidden="true" size={17} />设置</NavLink>
        </nav>
        <div className="ml-3 flex h-full min-w-0 flex-1 items-center gap-3" ref={setEditorTarget}/>
        <ThemeToggle/>
      </header>
      <main className="h-full bg-app"><Outlet /></main>
    </div>
  </AppHeaderContext.Provider>
}

function RequiredSetupGate({status, available, configured, fonts, onReady}: {
  status: SetupStatus
  available: ModelDescriptor[]
  configured: ModelConfiguration[]
  fonts: FontResource[]
  onReady: (status: SetupStatus) => void
}) {
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
      content:({close}) => <RecommendedConfigurationWizard
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
            throw new Error(installFailures.map(item => `${setupStageLabel(item.kind)}：${item.error || '安装失败'}`).join('；') || '推荐配置安装失败')
          }
          const verified = await api.models.verifySetup()
          if (!verified.ready) {
            throw new Error(verified.stages.filter(item => item.status !== 'ready').map(item => `${setupStageLabel(item.kind)}：${item.message}`).join('；'))
          }
          close()
          onReady(verified)
        }}
      />,
    })
  }, [available, configured, fonts, onReady, openDialog, status.first_run])
  return <div className="relative grid h-full min-h-[620px] place-items-center overflow-hidden bg-app p-8">
    <div className="absolute inset-0 opacity-50 [background-image:radial-gradient(circle_at_50%_20%,rgb(11_210_166/.16),transparent_38%),linear-gradient(var(--workspace-grid-line)_1px,transparent_1px),linear-gradient(90deg,var(--workspace-grid-line)_1px,transparent_1px)] [background-size:auto,42px_42px,42px_42px]"/>
    <div className="relative w-[min(720px,100%)] rounded-2xl bg-surface p-8 shadow-dialog"><div className="flex items-start gap-4"><span className="grid size-12 shrink-0 place-items-center rounded-xl bg-accent/15 text-accent"><LockKeyhole size={23}/></span><div><span className="font-mono text-[9px] tracking-[2px] text-accent">MANGAFLOW · FIRST RUN</span><h1 className="mb-2 mt-2 text-2xl">工作台正在等待初始化</h1><p className="m-0 text-xs leading-relaxed text-muted">完成硬件检测、模型安装和六项配置自检后，项目与编辑工作台会自动解锁。</p></div></div><div className="mt-6 grid grid-cols-3 gap-2">{status.stages.map(item => <div key={item.kind} className="flex min-h-[58px] items-center gap-2.5 rounded-lg bg-panel px-3 py-2"><span className={cn('grid size-6 shrink-0 place-items-center rounded-md', item.status === 'ready' ? 'bg-success/10 text-success' : 'bg-warning/10 text-warning')}>{item.status === 'ready' ? <Check size={13}/> : <CircleAlert size={13}/>}</span><span className="min-w-0"><strong className="block text-[9px]">{setupStageLabel(item.kind)}</strong><small className="mt-1 block truncate text-[8px] text-muted">{item.status === 'ready' ? '已就绪' : item.message || '等待配置'}</small></span></div>)}</div><div className="mt-5 flex items-center gap-2 text-[9px] text-muted"><Sparkles size={13} className="text-accent"/>推荐配置窗口已自动打开，安装期间请不要关闭页面。</div></div>
  </div>
}

function SetupLoadingScreen() {
  return <PageLoader label="正在检查工作台配置" detail="正在验证 Provider、设备运行时与字体资源"/>
}

function SetupFailureScreen({message, onRetry}: {message: string, onRetry: () => void}) {
  return <div className="grid h-full min-h-[620px] place-items-center bg-app p-8"><div className="w-[min(480px,100%)] rounded-2xl bg-surface p-8 text-center shadow-dialog"><CircleAlert className="mx-auto text-danger" size={30}/><h1 className="mb-2 mt-4 text-lg">无法检查工作台配置</h1><p className="mb-5 mt-0 text-xs leading-relaxed text-muted">{message}</p><button type="button" className={buttonClass} onClick={onRetry}><RefreshCw size={15}/>重新检测</button></div></div>
}

function setupStageLabel(kind: string): string {
  return {detection:'文字检测', inpainting:'图像修复', ocr:'OCR 识别', rendering:'排版渲染', translation:'云端翻译', font:'字体资源'}[kind] || kind
}

export function EmptyState({ title, detail, action }: {title: string, detail: string, action?: React.ReactNode}) {
  return <div className="flex min-h-[350px] flex-col items-center justify-center rounded-2xl border border-dashed border-line bg-panel p-8 text-center text-muted shadow-soft"><BookOpen size={36} /><h3 className="mb-0 mt-4 text-base text-ink">{title}</h3><p className="mb-6 mt-2 max-w-[460px] text-[13px] leading-6">{detail}</p>{action}</div>
}

export function Loading({ label = '正在加载…' }: {label?: string}) {
  return <PageLoader label={label}/>
}
