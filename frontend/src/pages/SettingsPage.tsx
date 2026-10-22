import {t, useLocale} from '../i18n'
import {Check, ChevronDown, ChevronRight, Cpu, Download, RefreshCw, Sparkles} from 'lucide-react'
import { FormEvent, useCallback, useEffect, useRef, useState } from 'react'
import {ExecutionSettings} from '../components/ExecutionSettings'
import {OCRConcurrencyFields} from '../components/OCRConcurrencyFields'
import {NumberControl, SelectControl} from '../components/FormControls'
import {ApiKeyInput} from '../components/ApiKeyInput'
import { useGlobalDialog } from '../components/GlobalDialog'
import {useGlobalAlert} from '../components/GlobalAlert'
import {ButtonLoading, IndeterminateProgress} from '../components/LoadingUI'
import {SettingsHeading, SettingsLayout} from '../components/SettingsLayout'
import {SETTINGS_PAGES, type SettingsPageId} from '../features/settings/navigation'
import {SettingsSkeleton} from '../components/SettingsSkeleton'
import {ShortcutSettings} from '../components/ShortcutSettings'
import {SettingsField, SettingsSection} from '../components/SettingsControls'
import {TypographySettings} from '../components/TypographySettings'
import {useContentReveal} from '../hooks/useContentReveal'
import {LanguageSwitcher} from '../components/LanguageSwitcher'
import {FontManager} from '../components/FontManager'
import { DEFAULT_FONT_FAMILIES } from '../constants/fonts'
import { DEFAULT_OCR_PROMPT } from '../constants/ocr'
import { api } from '../services/api'
import type { DeviceProfile, FontResource, ModelConfiguration, ModelDescriptor } from '../types'
import {buttonClass, cn, inputClass, textareaClass} from '../ui'

export function SettingsPage() {
  useLocale()
  const [available, setAvailable] = useState<ModelDescriptor[]>([])
  const [configured, setConfigured] = useState<ModelConfiguration[]>([])
  const [fonts, setFonts] = useState<FontResource[]>([])
  const [loading, setLoading] = useState(true)
  const [installing, setInstalling] = useState(false)
  const [active, setActive] = useState<SettingsPageId>('detection')
  const [visited, setVisited] = useState<SettingsPageId[]>(['detection'])
  const [loadError, setLoadError] = useState('')
  const [loadAttempt, setLoadAttempt] = useState(0)
  const installBusy = useRef(false)
  const mounted = useRef(true)
  const {confirm} = useGlobalDialog()
  const {alert} = useGlobalAlert()
  useEffect(() => {
    mounted.current = true
    return () => {mounted.current = false}
  }, [])
  const refreshModels = () => api.models.list().then(result => {setAvailable(result.available); setConfigured(result.configured)})
  const refreshFonts = () => api.fonts.list().then(setFonts)
  const navigate = (id: SettingsPageId) => {
    setActive(id)
    setVisited(current => current.includes(id) ? current : [...current, id])
  }
  const showRecommendedDialog = async () => {
    if (installBusy.current) return
    installBusy.current = true
    setInstalling(true)
    try {
      let result = await api.models.recommendedStatus()
      if (!mounted.current) return
      if (result.state !== 'installing' && result.installed && result.configured) {
        void alert({title:t("安装推荐配置"),message:t("配置已安装"),tone:'success'})
        return
      }
      if (result.state !== 'installing' && !(result.installed && result.configured)) {
        if (!result.installed) {
          const accepted = await confirm({
            title:t("安装推荐配置"),
            message:t("是否下载并安装以下配置？\nRF-DETR\nPaddleOCR-VL 1.6\nLaMa 复杂背景修复\nPillow 排版渲染"),
            confirmLabel:t("确认安装"),
            tone:'info',
          })
          if (!accepted || !mounted.current) return
        }
        result = await api.models.installRecommended()
      }
      // Installation survives page navigation; re-opening this action rejoins
      // the same backend job instead of creating another model download.
      while (result.state === 'installing') {
        await new Promise(resolve => window.setTimeout(resolve, 1200))
        if (!mounted.current) return
        result = await api.models.recommendedStatus()
      }
      if (!mounted.current) return
      if (result.state === 'error' || !result.installed || !result.configured) {
        throw new Error(result.error || t("部分模型安装失败"))
      }
      await refreshModels()
      if (mounted.current) void alert({title:t("安装推荐配置"),message:t("配置已安装"),tone:'success'})
    } catch (reason) {
      if (mounted.current) void alert({title:t("安装失败"),message:reason instanceof Error ? reason.message : String(reason),tone:'danger'})
    } finally {
      installBusy.current = false
      if (mounted.current) setInstalling(false)
    }
  }
  useEffect(() => {
    let disposed = false
    setLoading(true); setLoadError('')
    void Promise.all([api.models.list(), api.fonts.list()])
      .then(([models, fontList]) => {
        if (disposed) return
        setAvailable(models.available); setConfigured(models.configured); setFonts(fontList)
      })
      .catch(reason => {if (!disposed) setLoadError(reason instanceof Error ? reason.message : String(reason))})
      .finally(() => {if (!disposed) setLoading(false)})
    return () => {disposed = true}
  }, [loadAttempt])
  if (loading) return <SettingsSkeleton/>
  if (loadError) return <SettingsLayout><SettingsHeading id="detection"/><div className="settings-error-state"><p className="settings-error" role="alert">{t("无法读取设置：")}{loadError}</p><button type="button" className={buttonClass} onClick={() => setLoadAttempt(current => current+1)}><RefreshCw aria-hidden="true" size={16}/>{t("重新加载")}</button></div></SettingsLayout>
  return <SettingsLayout active={active} onNavigate={navigate} onInstall={() => {void showRecommendedDialog()}} installing={installing}>
        {visited.map(id => {const page = SETTINGS_PAGES.find(item => item.id === id)!;return <div key={id} hidden={active !== id}>
          <SettingsHeading id={id}/>
          {SETTINGS_PAGES.slice(0,5).some(item => item.id === id) ? <ModelForm active={active === id} available={available} configured={configured} fixedKind={id} initial={configurationForStage(configured,id)} translation={configurationForStage(configured,'translation')} onSave={async payload => {
            const target = configured.find(item => item.kind === id && item.provider === payload.provider)
            if (target) await api.models.update(target.id,payload)
            else await api.models.create(payload)
            await refreshModels()
            void alert({title:t("保存成功"),message:t("{{v0}}配置已保存。", {v0:page.label}),tone:'success'})
          }}/> : id === 'fonts' ? <FontManager fonts={fonts} onChanged={async () => {await refreshFonts()}}/> : id === 'shortcuts' ? <ShortcutSettings onSaved={async shortcuts => {await api.preferences.update(shortcuts)}}/> : id === 'performance' ? <ExecutionSettings/> : id === 'language' ? <div className="settings-form"><SettingsField label={t('显示语言')}><LanguageSwitcher/></SettingsField></div> : <TypographySettings fonts={fonts}/>}
        </div>})}
  </SettingsLayout>
}

type RecommendationKind = 'detection' | 'inpainting' | 'ocr' | 'rendering' | 'translation' | 'font'
type RecommendationSelection = {kind: RecommendationKind, provider: string, device?: string, config?: Record<string, unknown>, api_key?: string}
type CandidateGuide = {
  provider: string
  title: string
  summary: string
  pros: string[]
  cons: string[]
  requirements: string[]
}

const RECOMMENDATION_STAGES: Array<{kind: RecommendationKind, label: string, hint: string}> = [
  {kind:'detection', get label(){return t("文字检测")}, get hint(){return t("定位图片中的候选文字区域")}},
  {kind:'inpainting', get label(){return t("图像修复")}, get hint(){return t("去除原文并恢复背景")}},
  {kind:'ocr', get label(){return t("OCR 识别")}, get hint(){return t("读取区域内的原文")}},
  {kind:'rendering', get label(){return t("排版渲染")}, get hint(){return t("把译文排进文本区域")}},
  {kind:'translation', get label(){return t("云端翻译")}, get hint(){return t("调用远程大模型生成译文")}},
  {kind:'font', get label(){return t("字体资源")}, get hint(){return t("提供中文排版字体")}},
]

const RECOMMENDATION_GUIDES: Record<RecommendationKind, CandidateGuide[]> = {
  detection: [
    {provider:'rfdetr', get title(){return t("RF-DETR 漫画版面检测")}, get summary(){return t("统一检测文字、气泡、拟声词和分镜，按像素包含关系关联，不强制合并文本框。")}, get pros(){return [t("漫画专用实例分割"), t("文字和气泡共用同一模型")]}, get cons(){return [t("CPU 推理较慢"), t("密集小字和装饰字仍需检查")]}, get requirements(){return [t("隔离 RF-DETR 运行时"), t("Koharu 模型权重约 161 MB；建议使用 GPU")]}},
    {provider:'opencv-fallback', get title(){return t("OpenCV 文本检测")}, get summary(){return t("基于阈值和形态学的轻量本地检测。")}, get pros(){return [t("启动快且无需模型权重"), t("低配置 CPU 也能运行")]}, get cons(){return [t("复杂背景容易漏检或误检"), t("竖排、倾斜和艺术字效果较弱")]}, get requirements(){return ['OpenCV', t("任意现代 CPU")]}},
  ],
  inpainting: [
    {provider:'lama', get title(){return t("LaMa 复杂背景修复")}, get summary(){return t("使用深度模型重建文字下方的纹理和人物背景。")}, get pros(){return [t("复杂纹理与跨区域结构恢复最好"), t("适合气泡外文字和人物背景")]}, get cons(){return [t("首次需下载权重"), t("CPU 可运行但耗时明显")]}, get requirements(){return ['PyTorch / simple-lama-inpainting', t("GPU 建议 4 GB+ 显存；CPU 建议 8 GB+ 内存")]}},
    {provider:'opencv', get title(){return t("OpenCV 快速修复")}, get summary(){return t("使用 Telea/Navier-Stokes 在遮罩周围插值。")}, get pros(){return [t("无需模型且速度快"), t("低内存 CPU 友好")]}, get cons(){return [t("复杂背景容易出现色块或模糊"), t("大面积文字恢复能力有限")]}, get requirements(){return ['OpenCV', t("任意现代 CPU")]}},
  ],
  ocr: [
    {provider:'paddleocr-vl-1.6', title:'PaddleOCR-VL 1.6', get summary(){return t("Koharu 默认的本地视觉语言识字模型，只读取现有文本框。")}, get pros(){return [t("支持多语言与复杂版面裁剪"), t("与检测、翻译配置独立")]}, get cons(){return [t("首次需下载约 2 GB 权重"), t("长文推理可能慢于轻量 OCR，需实测")]}, get requirements(){return [t("独立 Transformers 5 运行环境"), t("建议 GPU；不上传图片")]}},
    {provider:'manga-ocr', get title(){return t("MangaOCR 漫画识别")}, get summary(){return t("专门针对日文漫画训练的 Transformer OCR。")}, get pros(){return [t("日文漫画和竖排文本准确率高"), t("对漫画字体与标点更稳")]}, get cons(){return [t("只负责识别，不负责检测"), t("首次需下载模型且 CPU 较慢")]}, get requirements(){return ['PyTorch / Transformers', t("建议 8 GB+ 内存；GPU 建议 2 GB+ 显存")]}},
    {provider:'baberu-ocr', title:'Baberu OCR', get summary(){return t("Koharu 同款的日中英漫画气泡识字模型。")}, get pros(){return [t("本地运行"), t("支持日文、中文、英文")]}, get cons(){return [t("首次需要下载权重"), t("手写与小字效果需要实测")]}, get requirements(){return [t("独立 Transformers 5 运行环境")]}},
    {provider:'hayai-ocr', title:'Hayai OCR', get summary(){return t("Koharu 同款 Hayai OCR，按裁剪区域识别日中韩英。")}, get pros(){return [t("保留裁剪区域的宽高比例"), t("本地运行")]}, get cons(){return [t("不用于整页检测"), t("小字与艺术字效果需要实测")]}, get requirements(){return [t("独立 Transformers 5 运行环境")]}},
    {provider:'tesseract', title:'Tesseract OCR', get summary(){return t("传统 CPU OCR，适合清晰规则的印刷文字。")}, get pros(){return [t("轻量且完全离线"), t("无需 GPU")]}, get cons(){return [t("漫画竖排与艺术字准确率较低"), t("日文语言包必须存在")]}, get requirements(){return [t("Tesseract 与 jpn 语言包"), t("任意现代 CPU")]}},
  ],
  rendering: [
    {provider:'pillow', get title(){return t("Pillow 排版渲染")}, get summary(){return t("本地完成横排、真竖排、换列、描边和字号拟合。")}, get pros(){return [t("稳定可控且完全离线"), t("支持当前编辑器的全部文字样式")]}, get cons(){return [t("效果依赖文本框与字体设置"), t("不自动生成手写艺术字")]}, get requirements(){return ['Pillow', t("CPU 即可，至少一种 CJK 字体")]}},
  ],
  translation: [
    {provider:'google-web', get title(){return t("Google 网页翻译（免配置）")}, get summary(){return t("免登录、免密钥、免填模型，直接翻译原文。")}, get pros(){return [t("无需配置账号或接口"), t("只发送文字，不上传图片")]}, get cons(){return [t("网页接口可能限流或不可用"), t("不使用项目上下文，语境表现不如大模型")]}, get requirements(){return [t("能够连接 Google 翻译服务")]}},
    {provider:'openai-compatible', get title(){return t("自定义云端翻译")}, get summary(){return t("通过 OpenAI、Responses 或 Anthropic 兼容 API 调用云端模型。")}, get pros(){return [t("可选择高质量大模型"), t("支持上下文、角色语气和结构化 Region ID")]}, get cons(){return [t("需要联网并可能产生费用"), t("安装后仍需填写接口、密钥和模型")]}, get requirements(){return ['API Base URL', t("API Key 与可用模型名"), t("稳定网络连接")]}},
  ],
  font: [
    {provider:'builtin', get title(){return t("内置 Noto CJK 字体包")}, get summary(){return t("默认提供黑体、宋体及其粗体，可直接用于中日韩排版。")}, get pros(){return [t("开箱即用且覆盖 CJK 字符"), t("横排和竖排均可使用")]}, get cons(){return [t("特殊美术风格需另行上传字体")]}, get requirements(){return [t("无需额外安装"), t("自定义字体可在“字体”配置中添加")]}},
  ],
}

export function RecommendedConfigurationWizard({available, configured, fonts, onCancel, onInstall, required = false, autoDetect = false}: {
  available: ModelDescriptor[]
  configured: ModelConfiguration[]
  fonts: FontResource[]
  onCancel?: () => void
  onInstall: (selections: RecommendationSelection[], preload: boolean) => Promise<void>
  required?: boolean
  autoDetect?: boolean
}) {
  useLocale()
  const [activeKind, setActiveKind] = useState<RecommendationKind>('detection')
  const contentRef = useContentReveal<HTMLElement>(activeKind)
  const [providers, setProviders] = useState<Record<RecommendationKind, string>>(() => initialRecommendationProviders(available, configured))
  const [devices, setDevices] = useState<Record<string, string>>(() => initialRecommendationDevices(available, configured))
  const [profile, setProfile] = useState<DeviceProfile | null>(null)
  const [deviceReasons, setDeviceReasons] = useState<Record<string, string>>({})
  const [detecting, setDetecting] = useState(false)
  const [preload, setPreload] = useState(true)
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState('')
  const currentTranslation = configured.find(item => item.kind === 'translation' && item.provider === 'openai-compatible' && item.is_default)
    || configured.find(item => item.kind === 'translation' && item.provider === 'openai-compatible')
  const currentTranslationConfig = currentTranslation?.provider === 'openai-compatible' ? currentTranslation.config : {}
  const initialTranslationModel = String(currentTranslationConfig.model || '')
  const [translationProtocol, setTranslationProtocol] = useState(String(currentTranslationConfig.api_protocol || 'auto'))
  const [translationBaseUrl, setTranslationBaseUrl] = useState(String(currentTranslationConfig.base_url || ''))
  const [translationApiKey, setTranslationApiKey] = useState('')
  const [translationModel, setTranslationModel] = useState(initialTranslationModel)
  const [translationModels, setTranslationModels] = useState<string[]>(initialTranslationModel ? [initialTranslationModel] : [])
  const [discoveringModels, setDiscoveringModels] = useState(false)
  const [modelDiscoveryError, setModelDiscoveryError] = useState('')
  const [translationVerified, setTranslationVerified] = useState(false)
  const autoDetectStarted = useRef(false)
  const activeStage = RECOMMENDATION_STAGES.find(stage => stage.kind === activeKind)!
  const candidates = RECOMMENDATION_GUIDES[activeKind].filter(candidate => activeKind === 'font' || available.some(item => item.kind === activeKind && item.name === candidate.provider))

  const detectAndRecommend = useCallback(async () => {
    setDetecting(true); setError('')
    try {
      const result = await api.models.deviceProfile()
      const memory = result.cpu.memory_gb ?? 8
      const capableForLargeModels = result.gpu.available || memory >= 8
      const pick = (kind: RecommendationKind, preferred: string, fallback: string) => {
        const preferredDescriptor = available.find(item => item.kind === kind && item.name === preferred)
        if (preferredDescriptor?.installed && capableForLargeModels) return preferred
        const fallbackDescriptor = available.find(item => item.kind === kind && item.name === fallback)
        return fallbackDescriptor?.installed ? fallback : preferredDescriptor ? preferred : fallback
      }
      const nextProviders: Record<RecommendationKind, string> = {
        detection: pick('detection', 'rfdetr', 'opencv-fallback'),
        inpainting: pick('inpainting', 'lama', 'opencv'),
        ocr: pick('ocr', 'paddleocr-vl-1.6', 'manga-ocr'),
        rendering: 'pillow',
        translation: 'openai-compatible',
        font: 'builtin',
      }
      setProviders(nextProviders)
      setDevices(current => ({
        ...current,
        detection: nextProviders.detection === 'opencv-fallback' ? 'cpu' : 'auto',
        inpainting: nextProviders.inpainting === 'opencv' ? 'cpu' : 'auto',
        ocr: nextProviders.ocr === 'tesseract' ? 'cpu' : 'auto',
      }))
      setDeviceReasons({
        detection: nextProviders.detection === 'rfdetr' ? t("使用统一的漫画版面检测与气泡分割模型。") : t("当前资源更适合轻量 OpenCV 检测。"),
        inpainting: nextProviders.inpainting === 'lama' ? t("设备资源足以运行复杂背景修复。") : t("内存或运行时受限，优先保证处理速度。"),
        ocr: nextProviders.ocr === 'paddleocr-vl-1.6' ? t("使用 Koharu 默认的本地视觉语言识字模型。") : t("保留可用的本地 OCR 配置。"),
        rendering: t("Pillow 使用 CPU 即可稳定完成排版。"),
        translation: t("云端翻译不占用本机 GPU，选择通用兼容接口。"),
        font: t("内置字体无需下载并覆盖中日韩字符。"),
      })
      setProfile(result)
    } catch (reason) {
      setError(t("设备检测失败：{{v0}}", {v0: reason instanceof Error ? reason.message : String(reason)}))
    } finally {
      setDetecting(false)
    }
  }, [available])

  useEffect(() => {
    if (!autoDetect || autoDetectStarted.current) return
    autoDetectStarted.current = true
    void detectAndRecommend()
  }, [autoDetect, detectAndRecommend])

  const discoverTranslationModels = async () => {
    const baseUrl = translationBaseUrl.trim()
    if (!/^https?:\/\//i.test(baseUrl)) {
      setModelDiscoveryError(t("请输入以 http:// 或 https:// 开头的 API Base URL。"))
      return
    }
    if (!translationApiKey.trim() && !currentTranslation?.has_api_key) {
      setModelDiscoveryError(t("请先填写 API Key。"))
      return
    }
    setDiscoveringModels(true); setModelDiscoveryError('')
    try {
      const result = await api.models.discover({
        base_url: baseUrl,
        api_protocol: translationProtocol,
        api_key: translationApiKey.trim() || undefined,
        config_id: currentTranslation?.id,
      })
      setTranslationBaseUrl(result.base_url)
      setTranslationModels(result.models)
      setTranslationModel(current => result.models.includes(current) ? current : result.models[0] || '')
      setTranslationVerified(true)
    } catch (reason) {
      setTranslationModels(translationModel ? [translationModel] : [])
      setModelDiscoveryError(reason instanceof Error ? reason.message : String(reason))
    } finally {
      setDiscoveringModels(false)
    }
  }

  const submit = async () => {
    if (required && !profile) {
      setError(t("首次启动必须先完成设备检测，才能生成可验证的运行配置。"))
      return
    }
    if (providers.translation === 'openai-compatible' && (!translationBaseUrl.trim() || !translationModel || (!translationApiKey.trim() && !currentTranslation?.has_api_key))) {
      setActiveKind('translation')
      setError(t("请填写云端翻译 API 地址和密钥，并成功获取、选择一个模型。"))
      return
    }
    if (required && providers.translation === 'openai-compatible' && !translationVerified) {
      setActiveKind('translation')
      setError(t("请点击“连接并获取模型”，确认云端翻译接口能够正常访问。"))
      return
    }
    setSubmitting(true); setError('')
    const selections = RECOMMENDATION_STAGES.map(stage => ({
      kind: stage.kind,
      provider: providers[stage.kind],
      device: ['detection', 'inpainting', 'ocr'].includes(stage.kind)
        ? concreteDevice(devices[stage.kind], available.find(item => item.kind === stage.kind && item.name === providers[stage.kind]))
        : undefined,
      config: stage.kind === 'translation' && providers.translation === 'openai-compatible' ? {
        ...PROVIDER_PRESETS['translation:openai-compatible'].config,
        api_protocol: translationProtocol,
        base_url: translationBaseUrl.trim(),
        model: translationModel,
      } : undefined,
      api_key: stage.kind === 'translation' && providers.translation === 'openai-compatible' && translationApiKey.trim() ? translationApiKey.trim() : undefined,
    }))
    try {
      await onInstall(selections, preload)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason))
      setSubmitting(false)
    }
  }

  const gpuName = profile?.gpu.devices[0]?.name
  const hardwareSummary = profile
    ? t("{{v0}} 线程 CPU · {{v1}} · {{v2}}", {v0: profile.cpu.logical_cores, v1: profile.cpu.memory_gb ? t("{{v0}} GB 内存", {v0: profile.cpu.memory_gb}) : t("内存未知"), v2: gpuName ? `${gpuName} (${profile.gpu.devices[0].memory_gb} GB)` : t("未检测到可用 GPU")})
    : t("尚未检测设备")
  const selectedCandidate = candidates.find(candidate => candidate.provider === providers[activeKind])
  const selectedDescriptor = available.find(item => item.kind === activeKind && item.name === providers[activeKind])
  return <div className="settings-recommendations">
    <div className="recommendation-scroll">
      {error && <p className="settings-error" role="alert">{error}</p>}
      <div className="recommendation-hardware">
        <div className="recommendation-hardware-info"><Cpu aria-hidden="true" size={18}/><span>{profile ? hardwareSummary : t("设备尚未检测")}</span></div>
        <button type="button" className={buttonClass} disabled={detecting || submitting} onClick={() => void detectAndRecommend()}>{detecting ? <ButtonLoading label={t("检测中…")}/> : <><Sparkles aria-hidden="true" size={16}/>{profile ? t("重新推荐") : t("自动推荐")}</>}</button>
      </div>
      {profile && <div className="recommendation-runtimes" role="status"><span>PyTorch CUDA {profile.runtimes.torch.cuda_available ? t("可用") : t("不可用")}</span><span>Paddle CUDA {profile.runtimes.paddle.cuda_available ? t("可用") : t("不可用")}</span></div>}
      <nav className="recommendation-navigation" aria-label={t("推荐配置阶段")}>
        {RECOMMENDATION_STAGES.map(stage => <button key={stage.kind} type="button" aria-current={activeKind === stage.kind ? 'step' : undefined} disabled={submitting} onClick={() => setActiveKind(stage.kind)}>{stage.label}</button>)}
      </nav>
      <section ref={contentRef} className="recommendation-content" aria-label={activeStage.label+t("方案")}>
        <header><h3>{activeKind === 'font' ? t("字体包") : t("选择模型")}</h3><span>{candidates.length} {' '}{t("个方案")}</span></header>
        <div className="recommendation-candidates">
          {!candidates.length && <p className="settings-error" role="alert">{t("当前没有可用方案，请检查模型运行依赖。")}</p>}
          {candidates.map(candidate => {
            const selected = providers[activeKind] === candidate.provider
            const descriptor = available.find(item => item.kind === activeKind && item.name === candidate.provider)
            const isCurrent = configured.some(item => item.kind === activeKind && item.provider === candidate.provider && item.is_default)
            const unavailableDuringSetup = required && activeKind === 'translation' && candidate.provider === 'passthrough'
            return <button key={candidate.provider} type="button" aria-pressed={selected} disabled={submitting || unavailableDuringSetup} className={cn('recommendation-candidate-select', selected && 'is-selected')} onClick={() => setProviders(current => ({...current, [activeKind]:candidate.provider}))}>
              <span className="recommendation-radio" aria-hidden="true">{selected && <Check size={12}/>}</span>
              <span className="recommendation-candidate-info"><span className="recommendation-candidate-title"><strong>{candidate.title}</strong>{isCurrent && <span className="recommendation-current">{t("当前配置")}</span>}{descriptor && !descriptor.installed && <span className="recommendation-warning">{t("缺少运行依赖")}</span>}{unavailableDuringSetup && <span className="recommendation-warning">{t("首次启动不可用")}</span>}</span><small>{candidate.summary}</small></span>
            </button>
          })}
        </div>
        {selectedCandidate && <div className="recommendation-options">
          {selectedDescriptor?.devices.includes('cuda') && activeKind !== 'font' && <div className="recommendation-device"><span>{t("运行设备")}</span><SelectControl ariaLabel={selectedCandidate.title+t("运行设备")} disabled={submitting} value={devices[activeKind] || 'auto'} options={deviceOptions(selectedDescriptor)} onChange={value => setDevices(current => ({...current, [activeKind]:value}))}/></div>}
          {activeKind === 'translation' && selectedCandidate.provider === 'openai-compatible' && <div className="settings-grid recommendation-connection">
            <div><span className="recommendation-field-label">{t("接口协议")}</span><SelectControl ariaLabel={t("首次配置翻译 API 协议")} disabled={submitting} value={translationProtocol} options={TRANSLATION_PROTOCOLS} onChange={value => {setTranslationProtocol(value); setTranslationVerified(false)}}/></div>
            <label>{t("API 地址")}<input className={inputClass} name="recommended_base_url" type="url" disabled={submitting} value={translationBaseUrl} onChange={event => {setTranslationBaseUrl(event.target.value); setModelDiscoveryError(''); setTranslationVerified(false)}} placeholder="https://api.example.com/v1"/></label>
            <label className="col-span-full">{t("API 密钥")}<ApiKeyInput name="recommended_api_key" hasSavedKey={Boolean(currentTranslation?.has_api_key)} disabled={submitting} value={translationApiKey} onChange={event => {setTranslationApiKey(event.target.value); setModelDiscoveryError(''); setTranslationVerified(false)}} placeholder={t("请输入云端接口密钥")}/></label>
            <div className="col-span-full"><span className="recommendation-field-label">{t("模型")}</span><div className="settings-model-fetch"><SelectControl ariaLabel={t("首次配置翻译模型")} value={translationModel} disabled={submitting || discoveringModels || !translationModels.length} placeholder={discoveringModels ? t("正在获取模型…") : t("请先连接接口获取模型")} options={translationModels.map(item => ({value:item, label:item}))} onChange={setTranslationModel}/><button type="button" className={buttonClass} disabled={submitting || discoveringModels || !translationBaseUrl.trim()} onClick={() => void discoverTranslationModels()}>{discoveringModels ? <ButtonLoading label={t("连接中…")}/> : <><RefreshCw aria-hidden="true" size={16}/>{translationVerified ? t("已通过连接检测") : t("连接并获取模型")}</>}</button></div>{modelDiscoveryError && <p className="settings-error" role="alert">{modelDiscoveryError}</p>}{translationVerified && <p className="settings-notice" role="status">{t("接口连接检测通过")}</p>}</div>
          </div>}
          {activeKind === 'font' && <p className="settings-privacy">{DEFAULT_FONT_FAMILIES.length} {' '}{t("种内置字体")}{fonts.length ? ' · '+fonts.length+t(" 种自定义字体") : ''}</p>}
          <details className="recommendation-guide" key={activeKind+':'+selectedCandidate.provider}><summary>{t("效果与运行要求")}<ChevronDown aria-hidden="true" size={16}/></summary><div className="recommendation-guide-grid"><GuideList title={t("优势")} items={selectedCandidate.pros} tone="success"/><GuideList title={t("注意")} items={selectedCandidate.cons} tone="warning"/><GuideList title={t("运行要求")} items={selectedCandidate.requirements} tone="neutral"/></div>{deviceReasons[activeKind] && <p className="settings-privacy">{deviceReasons[activeKind]}</p>}</details>
        </div>}
      </section>
    </div>
    {submitting && <div className="recommendation-progress"><IndeterminateProgress label={t("正在安装配置、加载模型并执行可用性检测…")}/></div>}
    <footer className="recommendation-footer">
      <button type="button" role="switch" aria-label={t("立即下载并加载本地模型")} aria-checked={preload} className="recommendation-preload" title={required ? t("首次启动需通过模型可用性检测") : t("关闭时仅保存配置，首次使用时再加载")} disabled={submitting || required} onClick={() => setPreload(value => !value)}><span className="recommendation-switch" aria-hidden="true"><i/></span><span>{t("立即加载本地模型")}</span></button>
      <div className="recommendation-actions">{!required && onCancel && <button type="button" className={buttonClass} disabled={submitting} onClick={onCancel}>{t("取消")}</button>}<button type="button" className={cn(buttonClass,'settings-primary')} disabled={submitting || detecting || (required && !profile)} onClick={() => void submit()}>{submitting ? <ButtonLoading label={t("正在安装并检测…")}/> : <><Download aria-hidden="true" size={16}/>{required ? t("安装、检测并进入工作台") : t("安装所选配置")}</>}</button></div>
    </footer>
  </div>
}

function GuideList({title, items, tone}: {title: string, items: string[], tone: 'success' | 'warning' | 'neutral'}) {
  useLocale()
  return <div className="recommendation-guide-list"><strong className={tone === 'success' ? 'text-success' : tone === 'warning' ? 'text-warning' : 'text-secondary'}>{title}</strong><ul>{items.map(item => <li key={item}>{item}</li>)}</ul></div>
}

function initialRecommendationProviders(available: ModelDescriptor[], configured: ModelConfiguration[]): Record<RecommendationKind, string> {
  const output = {} as Record<RecommendationKind, string>
  for (const stage of RECOMMENDATION_STAGES) {
    if (stage.kind === 'font') {output.font = 'builtin'; continue}
    const current = configurationForStage(configured, stage.kind)
    const guides = RECOMMENDATION_GUIDES[stage.kind]
    output[stage.kind] = current && guides.some(item => item.provider === current.provider)
      ? current.provider
      : preferredProvider(stage.kind, available.filter(item => item.kind === stage.kind))
  }
  return output
}

function initialRecommendationDevices(available: ModelDescriptor[], configured: ModelConfiguration[]): Record<string, string> {
  const output: Record<string, string> = {}
  for (const kind of ['detection', 'inpainting', 'ocr']) {
    const current = configurationForStage(configured, kind)
    const provider = current?.provider || preferredProvider(kind, available.filter(item => item.kind === kind))
    const descriptor = available.find(item => item.kind === kind && item.name === provider)
    output[kind] = concreteDevice(current?.config.device, descriptor)
  }
  return output
}

const TEXT_BOX_PADDING_SIDES = [['top','上'],['bottom','下'],['left','左'],['right','右']] as const

function paddingValue(config:Record<string,unknown>, side:typeof TEXT_BOX_PADDING_SIDES[number][0]):string {
  const padding=config.text_box_padding
  return String(padding && typeof padding === 'object' && !Array.isArray(padding)
    ? (padding as Record<string,unknown>)[side] ?? 3 : padding ?? 3)
}

function ModelForm({active, available, configured, translation, fixedKind, initial, onSave}: {active: boolean, available: ModelDescriptor[], configured: ModelConfiguration[], translation?: ModelConfiguration, fixedKind: string, initial?: ModelConfiguration, onSave: (payload: Record<string, unknown>) => Promise<void>}) {
  useLocale()
  const {alert} = useGlobalAlert()
  const [submitting, setSubmitting] = useState(false)
  const [submitError, setSubmitError] = useState('')
  const formRef = useRef<HTMLFormElement>(null)
  const [testing, setTesting] = useState(false)
  const testRequest = useRef(0)
  const testBusy = useRef(false)
  const invalidateTest = () => {testRequest.current++}
  const initialKind = initial?.kind || fixedKind || 'translation'
  const kind = initialKind
  const choices = available.filter(item => item.kind === kind && isSelectableProvider(item.kind, item.name))
  const [provider, setProvider] = useState(initial?.provider || preferredProvider(initialKind, available.filter(item => item.kind === initialKind)))
  const selected = choices.find(item => item.name === provider)
  const cloudOcr = kind === 'ocr' && provider === 'cloud-multimodal'
  const koharuOcr = kind === 'ocr' && ['paddleocr-vl-1.6', 'baberu-ocr', 'hayai-ocr'].includes(provider)
  const keylessTranslation = kind === 'translation' && provider === 'google-web'
  const cloud = cloudOcr || (kind === 'translation' && provider === 'openai-compatible')
  const canReuseTranslation = Boolean(translation?.has_api_key && translation.config.base_url && translation.config.model)
  const [reuseTranslation, setReuseTranslation] = useState(false)
  const useTranslation = cloudOcr && reuseTranslation
  const preset = providerPreset(kind, provider)
  const existing = initial?.kind === kind && initial.provider === provider ? initial : configured.find(item => item.kind === kind && item.provider === provider)
  const defaults = existing?.config || preset.config
  const providerDefaults=useRef({config:defaults,descriptor:selected})
  providerDefaults.current={config:defaults,descriptor:selected}
  const [device, setDevice] = useState(concreteDevice(defaults.device, selected))
  const [apiProtocol, setApiProtocol] = useState(String(defaults.api_protocol ?? 'auto'))
  const [baseUrl, setBaseUrl] = useState(String(defaults.base_url ?? ''))
  const initialModel = String(defaults.model ?? '')
  const [model, setModel] = useState(initialModel)
  const [modelOptions, setModelOptions] = useState<string[]>(initialModel ? [initialModel] : [])
  const [apiKey, setApiKey] = useState('')
  const [discovering, setDiscovering] = useState(false)
  const [discoveryError, setDiscoveryError] = useState('')
  const discoveryRequest = useRef(0)
  const savedKeyUsable = Boolean(existing?.has_api_key && sameApiOrigin(String(existing.config.base_url || ''), baseUrl))
  const invalidateDiscovery = () => {
    invalidateTest()
    discoveryRequest.current += 1
    setDiscovering(false)
    setDiscoveryError('')
  }

  useEffect(() => {
    // Reset only when the provider changes, not when a model list refreshes
    // or another settings page saves while this form has an unsaved draft.
    const {config,descriptor}=providerDefaults.current
    const testVersion = testRequest
    testVersion.current++
    discoveryRequest.current += 1
    setDiscovering(false)
    setDevice(concreteDevice(config.device, descriptor))
    const nextBaseUrl = String(config.base_url ?? '')
    const nextModel = String(config.model ?? '')
    setApiProtocol(String(config.api_protocol ?? 'auto'))
    setBaseUrl(nextBaseUrl)
    setModel(nextModel)
    setModelOptions(nextModel ? [nextModel] : [])
    setApiKey('')
    setDiscoveryError('')
    return () => {discoveryRequest.current += 1; testVersion.current++}
  }, [kind, provider])

  useEffect(() => {
    // A result from a form the user has left must not open a global popup.
    if (!active) {
      testRequest.current++
      discoveryRequest.current++
      setDiscovering(false)
    }
  }, [active])

  const discoverModels = async () => {
    const normalizedUrl = baseUrl.trim()
    if (!normalizedUrl) return
    if (!apiKey.trim() && !savedKeyUsable) return
    if (!/^https?:\/\//i.test(normalizedUrl)) {
      setDiscoveryError(t("请输入以 http:// 或 https:// 开头的 API Base URL"))
      return
    }
    invalidateTest()
    setDiscovering(true)
    setDiscoveryError('')
    const request = ++discoveryRequest.current
    try {
      const result = await api.models.discover({base_url: normalizedUrl, api_protocol: apiProtocol, api_key: apiKey.trim() || undefined, config_id: savedKeyUsable ? existing?.id : undefined})
      if (request !== discoveryRequest.current) return
      setModelOptions(result.models)
      setModel(current => current || (cloudOcr ? '' : result.models[0] || ''))
      setBaseUrl(result.base_url)
      setApiProtocol(result.protocol)
      void alert({title:t("获取模型"),message:t("已获取 {{v0}} 个模型",{v0:result.models.length}),tone:'success'})
    } catch (reason) {
      if (request !== discoveryRequest.current) return
      setModelOptions(model ? [model] : [])
      void alert({title:t("获取模型"),message:reason instanceof Error ? reason.message : String(reason),tone:'danger'})
    } finally {
      if (request === discoveryRequest.current) setDiscovering(false)
    }
  }
  const formPayload = (form: HTMLFormElement) => {
    const data = new FormData(form)
    const config: Record<string, unknown> = {...defaults}
    if (kind === 'detection' && provider === 'rfdetr') {
      // Native layout labels define text vs SFX; legacy classifier overrides
      // must not change which objects become translatable text regions.
      delete config.text_classification
      for (const [name,fallback] of [['text_threshold',0.25],['bubble_threshold',0.5],['panel_threshold',0.5],['containment_threshold',0.9]] as const) config[name]=Number(data.get(name) || fallback)
      config.text_box_padding=Object.fromEntries(TEXT_BOX_PADDING_SIDES.map(([side]) =>
        [side,Number(data.get(`text_box_padding_${side}`) ?? paddingValue(defaults,side))]))
    }
    for (const key of ['api_protocol','base_url','model','device','batch_size','timeout','retries','temperature','max_tokens','max_new_tokens','min_font_size','prompt','concurrency_mode','max_concurrency','requests_per_minute']) {
      if (!data.has(key)) continue
      const value = data.get(key)
      if (typeof value === 'string' && value.trim()) config[key] = ['batch_size','timeout','retries','temperature','max_tokens','max_new_tokens','min_font_size','max_concurrency','requests_per_minute'].includes(key) ? Number(value) : value.trim()
      else delete config[key]
    }
    const modelName = String(useTranslation ? translation?.config.model || '' : data.get('model') || '').trim()
    if (cloud && !modelName) {
      throw new Error(t("请先获取并选择模型。"))
    }
    if (cloud) delete config.device
    if (useTranslation) for (const key of ['base_url', 'model', 'api_protocol']) delete config[key]
    const generatedName = cloud && modelName ? `${cloudOcr ? t("多模态识字") : t("云端翻译")} · ${modelName}` : existing?.name || preset.title
    return {kind, provider, name:generatedName, api_key:useTranslation ? undefined : String(data.get('api_key') || '').trim() || undefined,
      reuse_translation:useTranslation, enabled:true, is_default:true, config}
  }
  const testConnection = async () => {
    if (testBusy.current || submitting || !formRef.current || !formRef.current.reportValidity()) return
    testBusy.current = true
    const request = ++testRequest.current
    setTesting(true)
    try {
      const payload = formPayload(formRef.current)
      const signature = JSON.stringify(payload)
      const result = await api.models.testConnection({...payload, config_id:existing?.id})
      if (request !== testRequest.current || !formRef.current || JSON.stringify(formPayload(formRef.current)) !== signature) return
      void alert({title:t('测试连接'),tone:result.ok ? 'success' : 'danger',message:result.ok
        ? result.mode === 'local' ? t('本地服务检测通过') : t('连接测试通过')
        : result.message || t('连接测试失败')})
    } catch (reason) {
      if (request === testRequest.current) void alert({title:t('测试连接'),message:reason instanceof Error ? reason.message : String(reason),tone:'danger'})
    } finally {testBusy.current=false; setTesting(false)}
  }
  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    if (submitting || testBusy.current) return
    let payload: ReturnType<typeof formPayload>
    try {payload=formPayload(event.currentTarget)}
    catch (reason) {setSubmitError(reason instanceof Error ? reason.message : String(reason));return}
    invalidateTest()
    setSubmitting(true); setSubmitError('')
    try {
      await onSave(payload)
      setApiKey('')
    } catch (reason) {
      const message=reason instanceof Error ? reason.message : String(reason)
      void alert({title:t("保存失败"),message,tone:'danger'})
    } finally {setSubmitting(false)}
  }
  return <form ref={formRef} className="settings-form" onSubmit={submit} onChangeCapture={invalidateTest}>
    {submitError && <p className="settings-error" role="alert">{submitError}</p>}
    {!selected && <p className="settings-error" role="alert">{t("当前阶段没有可用模型，请先安装推荐配置。")}</p>}
    <SettingsSection title={kind === 'translation' ? t("连接配置") : kind === 'ocr' ? t("识别模型") : t("模型与设备")}>
      <SettingsField label={kind === 'translation' ? t("服务商") : t("当前模型")}><SelectControl ariaLabel={t("当前模型")} value={provider} options={choices.map(item => ({value:item.name, label:providerLabel(kind,item.name)}))} onChange={next => {setProvider(next); setReuseTranslation(next === 'cloud-multimodal' && !configured.some(item => item.kind === kind && item.provider === next) && canReuseTranslation)}}/></SettingsField>
      {!cloud && kind !== 'translation' && <SettingsField label={t("运行设备")}>{kind === 'rendering' ? <span className="settings-readonly">CPU</span> : <SelectControl name="device" ariaLabel={t("运行设备")} value={device} options={deviceOptions(selected)} onChange={value=>{invalidateTest();setDevice(value)}}/>}</SettingsField>}
      {selected?.supports_batch && kind !== 'translation' && <SettingsField label={t("批大小")}><NumberControl key={provider+'-batch'} ariaLabel="Batch Size" name="batch_size" min={1} step={1} defaultValue={String(defaults.batch_size ?? 4)}/></SettingsField>}
      {kind === 'rendering' && <SettingsField label={t("最小字号")}><NumberControl key={provider+'-font'} ariaLabel={t("最小字号")} name="min_font_size" min={1} max={512} step={1} defaultValue={String(defaults.min_font_size ?? 10)}/></SettingsField>}
      {keylessTranslation && <p className="settings-privacy">{t("免登录、免密钥、免填模型。仅将原文发送到 Google，不上传图片或项目上下文；网页接口可能限流或不可用。")}</p>}
    </SettingsSection>
    {koharuOcr && <SettingsSection title={t("本地识别参数")}><div className="settings-grid" key={provider}>
      <label>{t("最大输出 Token 数")}<NumberControl ariaLabel={t("本地 OCR 最大输出 Token 数")} name="max_new_tokens" min={16} max={2048} step={16} defaultValue={String(defaults.max_new_tokens ?? 2048)}/></label>
      <label>{t("单区域超时（秒）")}<NumberControl ariaLabel={t("本地 OCR 超时")} name="timeout" min={10} max={600} step={10} defaultValue={String(defaults.timeout ?? 180)}/></label>
    </div></SettingsSection>}
    {kind === 'detection' && provider === 'rfdetr' && <>
      <SettingsSection title={t("检测参数")}><div className="settings-grid">{([['text_threshold',t("文字检测置信度"),0.25],['bubble_threshold',t("气泡检测置信度"),0.5],['panel_threshold',t("分镜检测置信度"),0.5],['containment_threshold',t("文字气泡包含率"),0.9]] as const).map(([name,label,fallback]) => <label key={provider+name}>{label}<NumberControl name={name} ariaLabel={label} min={name==='containment_threshold'?0.5:0.2} max={1} step={0.05} defaultValue={String(defaults[name] ?? fallback)}/></label>)}</div></SettingsSection>
      <SettingsSection title={t("文本框扩边")}><div className="settings-padding-grid">{TEXT_BOX_PADDING_SIDES.map(([side,label]) => <label key={provider+side}>{t(label)} (px)<NumberControl name={'text_box_padding_'+side} ariaLabel={t("文本框")+t(label)+t("扩边像素")} min={0} max={50} step={1} defaultValue={paddingValue(defaults,side)}/></label>)}</div></SettingsSection>
    </>}
    {cloud && <div key={provider}>
      {cloudOcr && <SettingsSection title={t("连接配置")}>
        <p className="settings-privacy">{t("图片会发送到你配置的服务，可能产生费用；请选择支持图片输入的模型。")}</p>
        <SettingsField label={t("连接配置")}><SelectControl ariaLabel={t("连接配置")} value={useTranslation ? 'translation' : 'custom'} options={[{value:'translation', label:t("使用当前翻译模型"), disabled:!canReuseTranslation}, {value:'custom', label:t("独立配置视觉模型")}]} onChange={value => {setReuseTranslation(value === 'translation'); invalidateDiscovery()}}/></SettingsField>
        {useTranslation && <p className="settings-readonly">{String(translation?.config.model)} · {String(translation?.config.base_url)}</p>}
      </SettingsSection>}
      {!useTranslation && <div className="settings-connection">
        <SettingsField label={t("接口协议")}><SelectControl name="api_protocol" ariaLabel={t("API 协议")} value={apiProtocol} options={TRANSLATION_PROTOCOLS} onChange={next => {setApiProtocol(next); invalidateDiscovery()}}/></SettingsField>
        <SettingsField label={t("API 地址")}><input className={inputClass} name="base_url" aria-label="API Base URL" type="url" required value={baseUrl} onChange={event => {setBaseUrl(event.target.value); invalidateDiscovery()}} placeholder="https://api.example.com/v1"/></SettingsField>
        <SettingsField label={t("API 密钥")}><ApiKeyInput name="api_key" aria-label="API Key" hasSavedKey={savedKeyUsable} required={!savedKeyUsable} value={apiKey} onChange={event => {setApiKey(event.target.value); invalidateDiscovery()}}/></SettingsField>
        <SettingsField label={t("模型名称")}><div className="settings-model-picker grid min-w-0 gap-3">
          <div className="settings-model-fetch">
            <SelectControl name="model" ariaLabel={cloudOcr ? t("选择 OCR 模型") : t("选择翻译模型")} value={model} disabled={discovering || !modelOptions.length} placeholder={discovering ? t("正在获取模型…") : t("请选择模型")} options={Array.from(new Set([...modelOptions,...(model ? [model] : [])])).map(item => ({value:item,label:item}))} onChange={value=>{invalidateTest();setModel(value)}}/>
            <button className={buttonClass} type="button" aria-label={t("重新获取模型列表")} disabled={discovering || !baseUrl.trim() || (!apiKey.trim() && !savedKeyUsable)} onClick={() => void discoverModels()}>{discovering ? <ButtonLoading label={t("获取中…")}/> : <><RefreshCw size={16}/>{t("获取模型")}</>}</button>
          </div>
          {discoveryError && <p role="alert" className="settings-error !m-0">{discoveryError}</p>}
        </div></SettingsField>
      </div>}
      <details className="settings-advanced" open={cloudOcr ? true : undefined}>
        <summary><ChevronRight size={16}/><span>{t("高级选项")}</span></summary>
        <div className="settings-section-body">
          <div className="settings-grid settings-grid-three">
            <label>{t("超时（秒）")}<NumberControl ariaLabel={t("接口超时")} name="timeout" min={10} max={cloudOcr ? 600 : undefined} step={5} defaultValue={String(defaults.timeout ?? 90)}/></label>
            <label>{t("失败重试")}<NumberControl ariaLabel={t("失败重试次数")} name="retries" min={0} max={5} step={1} defaultValue={String(defaults.retries ?? 2)}/></label>
            <label>Temperature<NumberControl ariaLabel="Temperature" name="temperature" min={0} max={cloudOcr ? 1 : 2} step={0.1} placeholder={cloudOcr ? t("模型默认") : undefined} defaultValue={String(defaults.temperature ?? (cloudOcr ? '' : 0.2))}/></label>
          </div>
          <SettingsField label={t("最大输出 Token 数")}><NumberControl ariaLabel={t("最大输出 Token 数")} name="max_tokens" min={128} max={32768} step={128} defaultValue={String(defaults.max_tokens ?? 2048)}/></SettingsField>
          <label className="settings-prompt">{cloudOcr ? t("识别 Prompt") : t("翻译 Prompt")}<textarea aria-label={cloudOcr ? t("识别 Prompt") : t("翻译 Prompt")} className={textareaClass} name="prompt" defaultValue={cloudOcr ? String(defaults.prompt || DEFAULT_OCR_PROMPT) : translationPrompt(defaults.prompt)}/></label>
        </div>
      </details>
    </div>}
    {cloudOcr && <div className="settings-generic"><OCRConcurrencyFields key={provider+'-concurrency'} defaults={defaults}/></div>}
    <footer className="settings-actions">
      <button type="button" className={buttonClass} disabled={testing || submitting || discovering || !selected} onClick={()=>void testConnection()}
        title={cloud || keylessTranslation ? t('云端测试会发送内置示例，可能产生少量费用。') : t('检查本地模型及运行依赖，首次加载可能较慢。')}>
        {testing ? <ButtonLoading label={t('正在测试…')}/> : t('测试连接')}
      </button>
      <button className={cn(buttonClass,'settings-primary')} disabled={testing || submitting || !selected} type="submit">{submitting ? <ButtonLoading label={t("正在保存…")}/> : t("保存配置")}</button>
    </footer>
  </form>
}
const TRANSLATION_PROTOCOLS = [
  {value: 'auto', get label(){return t("自动选择")}},
  {value: 'openai', label: 'OpenAI Chat Completions'},
  {value: 'responses', label: 'OpenAI Responses'},
  {value: 'anthropic', label: 'Anthropic Messages'},
]

const LEGACY_TRANSLATION_PROMPT = 'Translate manga dialogue naturally and preserve character voice. Return only valid JSON.'
const DEFAULT_TRANSLATION_PROMPT = `You are a professional manga localization translator. Translate every supplied region from the declared source language into the declared target language.

Requirements:
1. Preserve each character's personality, emotion, speaking style, politeness level, relationships, recurring terminology, names, honorifics, catchphrases, and continuity with the project context.
2. Produce natural dialogue that reads like an officially localized manga, not a literal machine translation. Keep wording concise enough to fit the original speech balloon while preserving meaning and emotional impact.
3. Handle narration, signs, captions, sound effects, and onomatopoeia appropriately for their function. Preserve meaningful Japanese honorifics when the target-language context benefits from them.
4. Use the glossary and project context consistently. Do not add explanations, translator notes, censorship, or information absent from the source.
5. Treat every Region ID as immutable. Never merge, split, rename, omit, reorder, or invent IDs, even when adjacent regions form one sentence.
6. Return only one valid JSON object whose keys exactly match all supplied Region IDs and whose values are translated strings. Do not use Markdown, code fences, comments, or any text outside the JSON object.`

const PROVIDER_PRESETS: Record<string, {label: string, title: string, config: Record<string, unknown>}> = {
  'ocr:paddleocr-vl-1.6': {label:'PaddleOCR-VL 1.6', get title(){return t("PaddleOCR-VL 1.6（Koharu 默认）")}, config:{device:'auto',max_new_tokens:2048,timeout:180}},
  'ocr:baberu-ocr': {label:'Baberu OCR', title:'Baberu OCR', config:{device:'auto',max_new_tokens:2048,timeout:180}},
  'ocr:hayai-ocr': {label:'Hayai OCR', title:'Hayai OCR', config:{device:'auto',max_new_tokens:2048,timeout:180}},
  'detection:rfdetr': {get label(){return t("RF-DETR 漫画版面检测")}, get title(){return t("推荐漫画版面检测（RF-DETR）")}, config:{device:'auto',text_threshold:0.25,bubble_threshold:0.5,panel_threshold:0.5,containment_threshold:0.9,text_box_padding:{top:3,bottom:3,left:3,right:3}}},
  'detection:opencv-fallback': {get label(){return t("OpenCV 文本检测")}, get title(){return t("本地 OpenCV 文本检测")}, config: {device: 'cpu'}},
  'ocr:manga-ocr': {get label(){return t("MangaOCR 漫画识别")}, get title(){return t("推荐漫画识别（MangaOCR）")}, config: {device: 'auto', model: 'kha-white/manga-ocr-base'}},
  'ocr:cloud-multimodal': {get label(){return t("多模态大模型识字")}, get title(){return t("多模态大模型识字")}, config: {api_protocol: 'auto', timeout: 90, retries: 2, max_tokens: 2048, prompt: DEFAULT_OCR_PROMPT}},
  'ocr:tesseract': {label: 'Tesseract OCR', title: 'Tesseract OCR', config: {device: 'cpu'}},
  'translation:openai-compatible': {get label(){return t("自定义云端翻译")}, get title(){return t("自定义云端翻译")}, config: {api_protocol: 'auto', timeout: 90, retries: 2, temperature: 0.2, max_tokens: 2048, prompt: DEFAULT_TRANSLATION_PROMPT}},
  'translation:google-web': {get label(){return t("Google 网页翻译（免配置）")}, get title(){return t("Google 网页翻译（免配置）")}, config:{timeout:30}},
  'inpainting:lama': {get label(){return t("LaMa 复杂背景修复")}, get title(){return t("推荐复杂修复（LaMa）")}, config: {device: 'auto'}},
  'inpainting:opencv': {get label(){return t("OpenCV 快速修复")}, get title(){return t("默认图像修复（OpenCV）")}, config: {device: 'cpu'}},
  'rendering:pillow': {get label(){return t("Pillow 排版渲染")}, get title(){return t("默认排版渲染（Pillow）")}, config: {min_font_size: 10}},
}

function isSelectableProvider(kind: string, provider: string): boolean {
  return !(kind === 'translation' && provider === 'passthrough')
    && !(kind === 'ocr' && ['paddleocr', 'cloud-ocr', 'review-fallback'].includes(provider))
}
function configurationForStage(configured: ModelConfiguration[], kind: string): ModelConfiguration | undefined {
  const candidates = configured.filter(item => item.kind === kind && isSelectableProvider(item.kind, item.provider))
  if (kind === 'translation') {
    return candidates.find(item => item.provider !== 'passthrough' && item.enabled && item.is_default)
      || candidates.find(item => item.provider === 'openai-compatible' && item.is_default)
      || candidates.find(item => item.provider === 'openai-compatible' && item.enabled)
      || candidates.find(item => item.provider === 'openai-compatible')
      || candidates.find(item => item.provider === 'google-web')
  }
  return candidates.find(item => item.enabled && item.is_default)
    || candidates.find(item => item.is_default)
    || candidates.find(item => item.enabled)
    || candidates[0]
}
function concreteDevice(value: unknown, selected?: ModelDescriptor): string {
  const configured = String(value || '').trim().toLowerCase()
  if (configured === 'cpu' || !selected?.devices.includes('cuda')) return 'cpu'
  if (configured.startsWith('cuda') || configured.startsWith('gpu')) return 'cuda:0'
  return 'auto'
}
function sameApiOrigin(left: string, right: string): boolean {
  try { return new URL(left).origin === new URL(right).origin } catch { return false }
}
function deviceOptions(selected?: ModelDescriptor) {
  return selected?.devices.includes('cuda')
    ? [{value:'auto', label:t("自动（GPU 优先）")}, {value:'cuda:0', label:'GPU（CUDA 0）'}, {value:'cpu', label:'CPU'}]
    : [{value:'cpu', label:'CPU'}]
}
function providerPreset(kind: string, provider: string) { return PROVIDER_PRESETS[`${kind}:${provider}`] || {label: provider, title: provider, config: {}} }
function providerLabel(kind: string, provider: string): string { return providerPreset(kind, provider).label }
function translationPrompt(value: unknown): string {
  const prompt = typeof value === 'string' ? value.trim() : ''
  return !prompt || prompt === LEGACY_TRANSLATION_PROMPT ? DEFAULT_TRANSLATION_PROMPT : prompt
}
function preferredProvider(kind: string, choices: ModelDescriptor[]): string {
  const preferred: Record<string, string> = {detection: 'rfdetr', ocr: 'paddleocr-vl-1.6', translation: 'openai-compatible', inpainting: 'lama', rendering: 'pillow'}
  const visible = choices.filter(item => isSelectableProvider(item.kind, item.name))
  return visible.some(item => item.name === preferred[kind]) ? preferred[kind] : visible[0]?.name || ''
}
