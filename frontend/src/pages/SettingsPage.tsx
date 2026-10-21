import {Check, ChevronDown, ChevronRight, Cpu, Download, RefreshCw, Sparkles, Trash2, Type, Upload} from 'lucide-react'
import { FormEvent, useEffect, useRef, useState } from 'react'
import {ExecutionSettings} from '../components/ExecutionSettings'
import {OCRConcurrencyFields} from '../components/OCRConcurrencyFields'
import {NumberControl, SelectControl} from '../components/FormControls'
import {ApiKeyInput} from '../components/ApiKeyInput'
import { useGlobalDialog } from '../components/GlobalDialog'
import {ButtonLoading, IndeterminateProgress} from '../components/LoadingUI'
import {SettingsHeading, SettingsLayout} from '../components/SettingsLayout'
import {SETTINGS_PAGES, type SettingsPageId} from '../features/settings/navigation'
import {SettingsSkeleton} from '../components/SettingsSkeleton'
import {ShortcutSettings} from '../components/ShortcutSettings'
import {SettingsField, SettingsSection} from '../components/SettingsControls'
import {TypographySettings} from '../components/TypographySettings'
import { DEFAULT_FONT_FAMILIES } from '../constants/fonts'
import { DEFAULT_OCR_PROMPT } from '../constants/ocr'
import { api } from '../services/api'
import type { DeviceProfile, FontResource, ModelBootstrapEntry, ModelConfiguration, ModelDescriptor } from '../types'
import {buttonClass, cn, iconButtonClass, inputClass, textareaClass} from '../ui'

export function SettingsPage() {
  const [available, setAvailable] = useState<ModelDescriptor[]>([])
  const [configured, setConfigured] = useState<ModelConfiguration[]>([])
  const [fonts, setFonts] = useState<FontResource[]>([])
  const [loading, setLoading] = useState(true)
  const [installing, setInstalling] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [noticeScope, setNoticeScope] = useState<SettingsPageId | null>(null)
  const [active, setActive] = useState<SettingsPageId>('detection')
  const [visited, setVisited] = useState<SettingsPageId[]>(['detection'])
  const [loadError, setLoadError] = useState('')
  const [loadAttempt, setLoadAttempt] = useState(0)
  const {openDialog} = useGlobalDialog()
  const refreshModels = () => api.models.list().then(result => {setAvailable(result.available); setConfigured(result.configured)})
  const refreshFonts = () => api.fonts.list().then(setFonts)
  const navigate = (id: SettingsPageId) => {
    setActive(id)
    setVisited(current => current.includes(id) ? current : [...current, id])
    setNotice(''); setError('')
  }
  const installRecommended = async (selections: RecommendationSelection[], preload: boolean): Promise<ModelBootstrapEntry[]> => {
    setNoticeScope(null)
    setInstalling(true)
    setError('')
    setNotice('')
    try {
      const result = await api.models.bootstrap({
        preload,
        upgrade_fallbacks: true,
        selections: selections.filter(item => item.kind !== 'font').map(({kind, provider, device, config, api_key}) => ({kind, provider, device, config, api_key})),
      })
      const ready = result.models.filter(item => item.status === 'ready').length
      const requiresConfiguration = result.models.filter(item => item.status === 'configuration_required')
      if (!result.ok) {
        const message = result.models.filter(item => ['error', 'dependency_missing'].includes(item.status)).map(item => `${kindLabel(item.kind)}：${item.error}`).join('；')
        setError(message)
        throw new Error(message || '部分模型安装失败')
      }
      setNotice(requiresConfiguration.length
        ? `所选配置已安装；云端翻译还需填写 API 地址、密钥和模型。${preload ? ` 本地模型已就绪（${ready}/${result.models.length}）。` : ''}`
        : `所选配置已安装${preload ? `，本地模型已就绪（${ready}/${result.models.length}）` : ''}。`)
      await refreshModels()
      return result.models
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason))
      throw reason
    } finally {
      setInstalling(false)
    }
  }
  const showRecommendedDialog = () => {
    openDialog({
      title:'安装推荐配置',
      size:'xlarge',
      className:'settings-dialog',
      dismissible: !installing,
      content:({close}) => <RecommendedConfigurationWizard
        available={available}
        configured={configured}
        fonts={fonts}
        onCancel={close}
        onInstall={async (selections, preload) => {
          await installRecommended(selections, preload)
          close()
        }}
      />,
    })
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
  if (loadError) return <SettingsLayout><SettingsHeading id="detection"/><div className="settings-error-state"><p className="settings-error" role="alert">无法读取设置：{loadError}</p><button type="button" className={buttonClass} onClick={() => setLoadAttempt(current => current+1)}><RefreshCw aria-hidden="true" size={16}/>重新加载</button></div></SettingsLayout>
  return <SettingsLayout active={active} onNavigate={navigate} onInstall={showRecommendedDialog} installing={installing}>
        {error && <p className="settings-error" role="alert">{error}</p>}
        {notice && (noticeScope === null || noticeScope === active) && <p className="settings-notice" role="status">{notice}</p>}
        {visited.map(id => {const page = SETTINGS_PAGES.find(item => item.id === id)!;return <div key={id} hidden={active !== id}>
          <SettingsHeading id={id}/>
          {SETTINGS_PAGES.slice(0,5).some(item => item.id === id) ? <ModelForm available={available} configured={configured} fixedKind={id} initial={configurationForStage(configured,id)} translation={configurationForStage(configured,'translation')} onSave={async payload => {
            const target = configured.find(item => item.kind === id && item.provider === payload.provider)
            if (target) await api.models.update(target.id,payload)
            else await api.models.create(payload)
            await refreshModels(); setNoticeScope(id); setNotice(`${page.label}配置已保存。`); setError('')
          }}/> : id === 'fonts' ? <FontManager initialFonts={fonts} onChanged={async message => {await refreshFonts(); setNoticeScope('fonts'); setNotice(message)}}/> : id === 'shortcuts' ? <ShortcutSettings onSaved={async shortcuts => {await api.preferences.update(shortcuts); setNoticeScope('shortcuts'); setNotice('快捷键设置已保存。')}}/> : id === 'performance' ? <ExecutionSettings/> : <TypographySettings fonts={fonts}/>}
        </div>})}
  </SettingsLayout>
}

export type RecommendationKind = 'detection' | 'inpainting' | 'ocr' | 'rendering' | 'translation' | 'font'
export type RecommendationSelection = {kind: RecommendationKind, provider: string, device?: string, config?: Record<string, unknown>, api_key?: string}
type CandidateGuide = {
  provider: string
  title: string
  summary: string
  pros: string[]
  cons: string[]
  requirements: string[]
}

const RECOMMENDATION_STAGES: Array<{kind: RecommendationKind, label: string, hint: string}> = [
  {kind:'detection', label:'文字检测', hint:'定位图片中的候选文字区域'},
  {kind:'inpainting', label:'图像修复', hint:'去除原文并恢复背景'},
  {kind:'ocr', label:'OCR 识别', hint:'读取区域内的原文'},
  {kind:'rendering', label:'排版渲染', hint:'把译文排进文本区域'},
  {kind:'translation', label:'云端翻译', hint:'调用远程大模型生成译文'},
  {kind:'font', label:'字体资源', hint:'提供中文排版字体'},
]

const RECOMMENDATION_GUIDES: Record<RecommendationKind, CandidateGuide[]> = {
  detection: [
    {provider:'rfdetr', title:'RF-DETR 漫画版面检测', summary:'统一检测文字、气泡、拟声词和分镜，按像素包含关系关联，不强制合并文本框。', pros:['漫画专用实例分割', '文字和气泡共用同一模型'], cons:['CPU 推理较慢', '密集小字和装饰字仍需检查'], requirements:['隔离 RF-DETR 运行时', 'Koharu 模型权重约 161 MB；建议使用 GPU']},
    {provider:'opencv-fallback', title:'OpenCV 文本检测', summary:'基于阈值和形态学的轻量本地检测。', pros:['启动快且无需模型权重', '低配置 CPU 也能运行'], cons:['复杂背景容易漏检或误检', '竖排、倾斜和艺术字效果较弱'], requirements:['OpenCV', '任意现代 CPU']},
  ],
  inpainting: [
    {provider:'lama', title:'LaMa 复杂背景修复', summary:'使用深度模型重建文字下方的纹理和人物背景。', pros:['复杂纹理与跨区域结构恢复最好', '适合气泡外文字和人物背景'], cons:['首次需下载权重', 'CPU 可运行但耗时明显'], requirements:['PyTorch / simple-lama-inpainting', 'GPU 建议 4 GB+ 显存；CPU 建议 8 GB+ 内存']},
    {provider:'hybrid', title:'LaMa 兼容修复', summary:'兼容旧项目命名，实际仍以 LaMa 处理所有修复区域。', pros:['兼容已有 hybrid 配置', '保持复杂修复质量'], cons:['新项目没有额外收益', '资源需求与 LaMa 相同'], requirements:['PyTorch / simple-lama-inpainting', 'GPU 建议 4 GB+ 显存']},
    {provider:'opencv', title:'OpenCV 快速修复', summary:'使用 Telea/Navier-Stokes 在遮罩周围插值。', pros:['无需模型且速度快', '低内存 CPU 友好'], cons:['复杂背景容易出现色块或模糊', '大面积文字恢复能力有限'], requirements:['OpenCV', '任意现代 CPU']},
  ],
  ocr: [
    {provider:'paddleocr-vl-1.6', title:'PaddleOCR-VL 1.6', summary:'Koharu 默认的本地视觉语言识字模型，只读取现有文本框。', pros:['支持多语言与复杂版面裁剪', '与检测、翻译配置独立'], cons:['首次需下载约 2 GB 权重', '长文推理可能慢于轻量 OCR，需实测'], requirements:['独立 Transformers 5 运行环境', '建议 GPU；不上传图片']},
    {provider:'manga-ocr', title:'MangaOCR 漫画识别', summary:'专门针对日文漫画训练的 Transformer OCR。', pros:['日文漫画和竖排文本准确率高', '对漫画字体与标点更稳'], cons:['只负责识别，不负责检测', '首次需下载模型且 CPU 较慢'], requirements:['PyTorch / Transformers', '建议 8 GB+ 内存；GPU 建议 2 GB+ 显存']},
    {provider:'baberu-ocr', title:'Baberu OCR', summary:'Koharu 同款的日中英漫画气泡识字模型。', pros:['本地运行', '支持日文、中文、英文'], cons:['首次需要下载权重', '手写与小字效果需要实测'], requirements:['独立 Transformers 5 运行环境']},
    {provider:'hayai-ocr', title:'Hayai OCR', summary:'Koharu 同款 Hayai OCR，按裁剪区域识别日中韩英。', pros:['保留裁剪区域的宽高比例', '本地运行'], cons:['不用于整页检测', '小字与艺术字效果需要实测'], requirements:['独立 Transformers 5 运行环境']},
    {provider:'tesseract', title:'Tesseract OCR', summary:'传统 CPU OCR，适合清晰规则的印刷文字。', pros:['轻量且完全离线', '无需 GPU'], cons:['漫画竖排与艺术字准确率较低', '日文语言包必须存在'], requirements:['Tesseract 与 jpn 语言包', '任意现代 CPU']},
    {provider:'review-fallback', title:'人工审核占位', summary:'不执行真实 OCR，只创建待人工填写的区域。', pros:['无依赖且不会伪造结果'], cons:['无法自动识别文本', '所有区域都需要手工录入'], requirements:['无需模型', '适合诊断或临时占位']},
  ],
  rendering: [
    {provider:'pillow', title:'Pillow 排版渲染', summary:'本地完成横排、真竖排、换列、描边和字号拟合。', pros:['稳定可控且完全离线', '支持当前编辑器的全部文字样式'], cons:['效果依赖文本框与字体设置', '不自动生成手写艺术字'], requirements:['Pillow', 'CPU 即可，至少一种 CJK 字体']},
  ],
  translation: [
    {provider:'google-web', title:'Google 网页翻译（免配置）', summary:'免登录、免密钥、免填模型，直接翻译原文。', pros:['无需配置账号或接口', '只发送文字，不上传图片'], cons:['网页接口可能限流或不可用', '不使用项目上下文，语境表现不如大模型'], requirements:['能够连接 Google 翻译服务']},
    {provider:'openai-compatible', title:'自定义云端翻译', summary:'通过 OpenAI、Responses 或 Anthropic 兼容 API 调用云端模型。', pros:['可选择高质量大模型', '支持上下文、角色语气和结构化 Region ID'], cons:['需要联网并可能产生费用', '安装后仍需填写接口、密钥和模型'], requirements:['API Base URL', 'API Key 与可用模型名', '稳定网络连接']},
  ],
  font: [
    {provider:'builtin', title:'内置 Noto CJK 字体包', summary:'默认提供黑体、宋体及其粗体，可直接用于中日韩排版。', pros:['开箱即用且覆盖 CJK 字符', '横排和竖排均可使用'], cons:['特殊美术风格需另行上传字体'], requirements:['无需额外安装', '自定义字体可在“字体”配置中添加']},
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
  const [activeKind, setActiveKind] = useState<RecommendationKind>('detection')
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

  const detectAndRecommend = async () => {
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
        detection: nextProviders.detection === 'rfdetr' ? '使用统一的漫画版面检测与气泡分割模型。' : '当前资源更适合轻量 OpenCV 检测。',
        inpainting: nextProviders.inpainting === 'lama' ? '设备资源足以运行复杂背景修复。' : '内存或运行时受限，优先保证处理速度。',
        ocr: nextProviders.ocr === 'paddleocr-vl-1.6' ? '使用 Koharu 默认的本地视觉语言识字模型。' : '保留可用的本地 OCR 配置。',
        rendering: 'Pillow 使用 CPU 即可稳定完成排版。',
        translation: '云端翻译不占用本机 GPU，选择通用兼容接口。',
        font: '内置字体无需下载并覆盖中日韩字符。',
      })
      setProfile(result)
    } catch (reason) {
      setError(`设备检测失败：${reason instanceof Error ? reason.message : String(reason)}`)
    } finally {
      setDetecting(false)
    }
  }

  useEffect(() => {
    if (!autoDetect || autoDetectStarted.current) return
    autoDetectStarted.current = true
    void detectAndRecommend()
  }, [autoDetect])

  const discoverTranslationModels = async () => {
    const baseUrl = translationBaseUrl.trim()
    if (!/^https?:\/\//i.test(baseUrl)) {
      setModelDiscoveryError('请输入以 http:// 或 https:// 开头的 API Base URL。')
      return
    }
    if (!translationApiKey.trim() && !currentTranslation?.has_api_key) {
      setModelDiscoveryError('请先填写 API Key。')
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
      setError('首次启动必须先完成设备检测，才能生成可验证的运行配置。')
      return
    }
    if (providers.translation === 'openai-compatible' && (!translationBaseUrl.trim() || !translationModel || (!translationApiKey.trim() && !currentTranslation?.has_api_key))) {
      setActiveKind('translation')
      setError('请填写云端翻译 API 地址和密钥，并成功获取、选择一个模型。')
      return
    }
    if (required && providers.translation === 'openai-compatible' && !translationVerified) {
      setActiveKind('translation')
      setError('请点击“连接并获取模型”，确认云端翻译接口能够正常访问。')
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
    ? `${profile.cpu.logical_cores} 线程 CPU · ${profile.cpu.memory_gb ? `${profile.cpu.memory_gb} GB 内存` : '内存未知'} · ${gpuName ? `${gpuName} (${profile.gpu.devices[0].memory_gb} GB)` : '未检测到可用 GPU'}`
    : '尚未检测设备'
  const selectedCandidate = candidates.find(candidate => candidate.provider === providers[activeKind])
  const selectedDescriptor = available.find(item => item.kind === activeKind && item.name === providers[activeKind])
  return <div className="settings-recommendations">
    <div className="recommendation-scroll">
      {error && <p className="settings-error" role="alert">{error}</p>}
      <div className="recommendation-hardware">
        <div className="recommendation-hardware-info"><Cpu aria-hidden="true" size={18}/><span>{profile ? hardwareSummary : '设备尚未检测'}</span></div>
        <button type="button" className={buttonClass} disabled={detecting || submitting} onClick={() => void detectAndRecommend()}>{detecting ? <ButtonLoading label="检测中…"/> : <><Sparkles aria-hidden="true" size={16}/>{profile ? '重新推荐' : '自动推荐'}</>}</button>
      </div>
      {profile && <div className="recommendation-runtimes" role="status"><span>PyTorch CUDA {profile.runtimes.torch.cuda_available ? '可用' : '不可用'}</span><span>Paddle CUDA {profile.runtimes.paddle.cuda_available ? '可用' : '不可用'}</span></div>}
      <nav className="recommendation-navigation" aria-label="推荐配置阶段">
        {RECOMMENDATION_STAGES.map(stage => <button key={stage.kind} type="button" aria-current={activeKind === stage.kind ? 'step' : undefined} disabled={submitting} onClick={() => setActiveKind(stage.kind)}>{stage.label}</button>)}
      </nav>
      <section className="recommendation-content" aria-label={activeStage.label+'方案'}>
        <header><h3>{activeKind === 'font' ? '字体包' : '选择模型'}</h3><span>{candidates.length} 个方案</span></header>
        <div className="recommendation-candidates">
          {!candidates.length && <p className="settings-error" role="alert">当前没有可用方案，请检查模型运行依赖。</p>}
          {candidates.map(candidate => {
            const selected = providers[activeKind] === candidate.provider
            const descriptor = available.find(item => item.kind === activeKind && item.name === candidate.provider)
            const isCurrent = configured.some(item => item.kind === activeKind && item.provider === candidate.provider && item.is_default)
            const unavailableDuringSetup = required && activeKind === 'translation' && candidate.provider === 'passthrough'
            return <button key={candidate.provider} type="button" aria-pressed={selected} disabled={submitting || unavailableDuringSetup} className={cn('recommendation-candidate-select', selected && 'is-selected')} onClick={() => setProviders(current => ({...current, [activeKind]:candidate.provider}))}>
              <span className="recommendation-radio" aria-hidden="true">{selected && <Check size={12}/>}</span>
              <span className="recommendation-candidate-info"><span className="recommendation-candidate-title"><strong>{candidate.title}</strong>{isCurrent && <span className="recommendation-current">当前配置</span>}{descriptor && !descriptor.installed && <span className="recommendation-warning">缺少运行依赖</span>}{unavailableDuringSetup && <span className="recommendation-warning">首次启动不可用</span>}</span><small>{candidate.summary}</small></span>
            </button>
          })}
        </div>
        {selectedCandidate && <div className="recommendation-options">
          {selectedDescriptor?.devices.includes('cuda') && activeKind !== 'font' && <div className="recommendation-device"><span>运行设备</span><SelectControl ariaLabel={selectedCandidate.title+'运行设备'} disabled={submitting} value={devices[activeKind] || 'auto'} options={deviceOptions(selectedDescriptor)} onChange={value => setDevices(current => ({...current, [activeKind]:value}))}/></div>}
          {activeKind === 'translation' && selectedCandidate.provider === 'openai-compatible' && <div className="settings-grid recommendation-connection">
            <div><span className="recommendation-field-label">接口协议</span><SelectControl ariaLabel="首次配置翻译 API 协议" disabled={submitting} value={translationProtocol} options={TRANSLATION_PROTOCOLS} onChange={value => {setTranslationProtocol(value); setTranslationVerified(false)}}/></div>
            <label>API 地址<input className={inputClass} name="recommended_base_url" type="url" disabled={submitting} value={translationBaseUrl} onChange={event => {setTranslationBaseUrl(event.target.value); setModelDiscoveryError(''); setTranslationVerified(false)}} placeholder="https://api.example.com/v1"/></label>
            <label className="col-span-full">API 密钥<ApiKeyInput name="recommended_api_key" hasSavedKey={Boolean(currentTranslation?.has_api_key)} disabled={submitting} value={translationApiKey} onChange={event => {setTranslationApiKey(event.target.value); setModelDiscoveryError(''); setTranslationVerified(false)}} placeholder="请输入云端接口密钥"/></label>
            <div className="col-span-full"><span className="recommendation-field-label">模型</span><div className="settings-model-fetch"><SelectControl ariaLabel="首次配置翻译模型" value={translationModel} disabled={submitting || discoveringModels || !translationModels.length} placeholder={discoveringModels ? '正在获取模型…' : '请先连接接口获取模型'} options={translationModels.map(item => ({value:item, label:item}))} onChange={setTranslationModel}/><button type="button" className={buttonClass} disabled={submitting || discoveringModels || !translationBaseUrl.trim()} onClick={() => void discoverTranslationModels()}>{discoveringModels ? <ButtonLoading label="连接中…"/> : <><RefreshCw aria-hidden="true" size={16}/>{translationVerified ? '已通过连接检测' : '连接并获取模型'}</>}</button></div>{modelDiscoveryError && <p className="settings-error" role="alert">{modelDiscoveryError}</p>}{translationVerified && <p className="settings-notice" role="status">接口连接检测通过</p>}</div>
          </div>}
          {activeKind === 'font' && <p className="settings-privacy">{DEFAULT_FONT_FAMILIES.length} 种内置字体{fonts.length ? ' · '+fonts.length+' 种自定义字体' : ''}</p>}
          <details className="recommendation-guide" key={activeKind+':'+selectedCandidate.provider}><summary>效果与运行要求<ChevronDown aria-hidden="true" size={16}/></summary><div className="recommendation-guide-grid"><GuideList title="优势" items={selectedCandidate.pros} tone="success"/><GuideList title="注意" items={selectedCandidate.cons} tone="warning"/><GuideList title="运行要求" items={selectedCandidate.requirements} tone="neutral"/></div>{deviceReasons[activeKind] && <p className="settings-privacy">{deviceReasons[activeKind]}</p>}</details>
        </div>}
      </section>
    </div>
    {submitting && <div className="recommendation-progress"><IndeterminateProgress label="正在安装配置、加载模型并执行可用性检测…"/></div>}
    <footer className="recommendation-footer">
      <button type="button" role="switch" aria-label="立即下载并加载本地模型" aria-checked={preload} className="recommendation-preload" title={required ? '首次启动需通过模型可用性检测' : '关闭时仅保存配置，首次使用时再加载'} disabled={submitting || required} onClick={() => setPreload(value => !value)}><span className="recommendation-switch" aria-hidden="true"><i/></span><span>立即加载本地模型</span></button>
      <div className="recommendation-actions">{!required && onCancel && <button type="button" className={buttonClass} disabled={submitting} onClick={onCancel}>取消</button>}<button type="button" className={cn(buttonClass,'settings-primary')} disabled={submitting || detecting || (required && !profile)} onClick={() => void submit()}>{submitting ? <ButtonLoading label="正在安装并检测…"/> : <><Download aria-hidden="true" size={16}/>{required ? '安装、检测并进入工作台' : '安装所选配置'}</>}</button></div>
    </footer>
  </div>
}

function GuideList({title, items, tone}: {title: string, items: string[], tone: 'success' | 'warning' | 'neutral'}) {
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

function FontManager({initialFonts, onChanged}: {initialFonts: FontResource[], onChanged: (message: string) => Promise<void>}) {
  const [items, setItems] = useState(initialFonts)
  const [preview, setPreview] = useState('你好，漫画世界。')
  const [busy, setBusy] = useState('')
  const [error, setError] = useState('')
  const input = useRef<HTMLInputElement>(null)
  const upload = async (file: File) => {
    setBusy('upload'); setError('')
    try {
      const uploaded = await api.fonts.upload(file)
      setItems(await api.fonts.list())
      await onChanged('字体“'+uploaded.name+'”已添加。')
    } catch (reason) {setError(reason instanceof Error ? reason.message : String(reason))}
    finally {setBusy(''); if (input.current) input.current.value = ''}
  }
  const remove = async (font: FontResource) => {
    setBusy(font.filename); setError('')
    try {await api.fonts.remove(font.filename); setItems(await api.fonts.list()); await onChanged('字体“'+font.name+'”已删除。')}
    catch (reason) {setError(reason instanceof Error ? reason.message : String(reason))}
    finally {setBusy('')}
  }
  useEffect(() => {
    if (typeof FontFace === 'undefined' || !document.fonts) return
    let disposed = false
    const registered: FontFace[] = []
    for (const font of items) {
      const source = font.url || '/media/'+font.path.split('/').map(encodeURIComponent).join('/')
      const face = new FontFace(font.name,'url('+JSON.stringify(source)+')')
      void face.load().then(loaded => {if (!disposed) {document.fonts.add(loaded); registered.push(loaded)}}).catch(() => undefined)
    }
    return () => {disposed = true; registered.forEach(font => document.fonts.delete(font))}
  }, [items])
  return <div className="settings-font-manager">
    {error && <p className="settings-error" role="alert">{error}</p>}
    <div className="settings-font-toolbar"><strong>{DEFAULT_FONT_FAMILIES.length} 种内置字体{items.length ? ' · '+items.length+' 种自定义字体' : ''}</strong><button className={buttonClass} type="button" disabled={Boolean(busy)} onClick={() => input.current?.click()}>{busy === 'upload' ? <ButtonLoading label="正在上传…"/> : <><Upload size={16}/>上传字体</>}</button><input ref={input} aria-label="上传字体文件" hidden type="file" accept=".ttf,.otf,font/ttf,font/otf" onChange={event => {const file=event.target.files?.[0]; if(file)void upload(file)}}/></div>
    <label className="settings-font-preview-input">预览文字<input className={inputClass} value={preview} onChange={event => setPreview(event.target.value)} maxLength={200}/></label>
    <SettingsSection title="可用字体"><div className="settings-font-list">
      {DEFAULT_FONT_FAMILIES.map(font => <article className="settings-font-row" key={font.name}><div className="settings-font-info"><Type size={20}/><div><strong>{font.name}</strong><small>{font.label}</small></div><span className="settings-badge">内置</span></div><p style={{fontFamily:'"'+font.name+'",'+(font.name.includes('Serif') ? 'serif' : 'sans-serif'),fontWeight:font.name.includes('Bold') ? 700 : 400}}>{preview || '你好，漫画世界。'}</p></article>)}
      {items.map(font => <article className="settings-font-row" key={font.filename}><div className="settings-font-info"><Type size={20}/><div><strong>{font.name}</strong><small>{font.filename}</small></div><button className={cn(iconButtonClass,'icon-button-danger')} type="button" disabled={Boolean(busy)} title={'删除'+font.name} aria-label={'删除'+font.name} onClick={() => void remove(font)}>{busy === font.filename ? <ButtonLoading compact label="正在删除"/> : <Trash2 aria-hidden="true" size={17}/>}</button></div><p style={{fontFamily:'"'+font.name+'",sans-serif'}}>{preview || '你好，漫画世界。'}</p></article>)}
    </div></SettingsSection>
  </div>
}
const TEXT_BOX_PADDING_SIDES = [['top','上'],['bottom','下'],['left','左'],['right','右']] as const

function paddingValue(config:Record<string,unknown>, side:typeof TEXT_BOX_PADDING_SIDES[number][0]):string {
  const padding=config.text_box_padding
  return String(padding && typeof padding === 'object' && !Array.isArray(padding)
    ? (padding as Record<string,unknown>)[side] ?? 3 : padding ?? 3)
}

function ModelForm({available, configured, translation, fixedKind, initial, onSave}: {available: ModelDescriptor[], configured: ModelConfiguration[], translation?: ModelConfiguration, fixedKind: string, initial?: ModelConfiguration, onSave: (payload: Record<string, unknown>) => Promise<void>}) {
  const [submitting, setSubmitting] = useState(false)
  const [submitError, setSubmitError] = useState('')
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
  const [device, setDevice] = useState(concreteDevice(defaults.device, selected))
  const [apiProtocol, setApiProtocol] = useState(String(defaults.api_protocol ?? 'auto'))
  const [baseUrl, setBaseUrl] = useState(String(defaults.base_url ?? ''))
  const initialModel = String(defaults.model ?? '')
  const [model, setModel] = useState(initialModel)
  const [modelOptions, setModelOptions] = useState<string[]>(initialModel ? [initialModel] : [])
  const [apiKey, setApiKey] = useState('')
  const [discovering, setDiscovering] = useState(false)
  const [discoveryError, setDiscoveryError] = useState('')
  const [discoveredEndpoint, setDiscoveredEndpoint] = useState('')
  const discoveryRequest = useRef(0)
  const savedKeyUsable = Boolean(existing?.has_api_key && sameApiOrigin(String(existing.config.base_url || ''), baseUrl))
  const invalidateDiscovery = () => {
    discoveryRequest.current += 1
    setDiscovering(false)
    setDiscoveryError('')
    setDiscoveredEndpoint('')
  }

  useEffect(() => {
    discoveryRequest.current += 1
    setDiscovering(false)
    setDevice(concreteDevice(defaults.device, selected))
    const nextBaseUrl = String(defaults.base_url ?? '')
    const nextModel = String(defaults.model ?? '')
    setApiProtocol(String(defaults.api_protocol ?? 'auto'))
    setBaseUrl(nextBaseUrl)
    setModel(nextModel)
    setModelOptions(nextModel ? [nextModel] : [])
    setApiKey('')
    setDiscoveryError('')
    setDiscoveredEndpoint('')
    return () => {discoveryRequest.current += 1}
  }, [kind, provider])

  const discoverModels = async () => {
    const normalizedUrl = baseUrl.trim()
    if (!normalizedUrl) return
    if (!apiKey.trim() && !savedKeyUsable) return
    if (!/^https?:\/\//i.test(normalizedUrl)) {
      setDiscoveryError('请输入以 http:// 或 https:// 开头的 API Base URL')
      return
    }
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
      setDiscoveredEndpoint(result.endpoint)
    } catch (reason) {
      if (request !== discoveryRequest.current) return
      setModelOptions(model ? [model] : [])
      setDiscoveryError(reason instanceof Error ? reason.message : String(reason))
      setDiscoveredEndpoint('')
    } finally {
      if (request === discoveryRequest.current) setDiscovering(false)
    }
  }
  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault(); const data = new FormData(event.currentTarget)
    const config: Record<string, unknown> = {...defaults}
    if (kind === 'detection' && provider === 'rfdetr') {
      // Native layout labels define text vs SFX; legacy classifier overrides
      // must not change which objects become translatable text regions.
      delete config.text_classification
      for (const [name,fallback] of [['text_threshold',0.25],['bubble_threshold',0.5],['panel_threshold',0.5],['containment_threshold',0.9]] as const) config[name]=Number(data.get(name) || fallback)
      config.text_box_padding=Object.fromEntries(TEXT_BOX_PADDING_SIDES.map(([side]) =>
        [side,Number(data.get(`text_box_padding_${side}`) ?? paddingValue(defaults,side))]))
    }
    for (const key of ['api_protocol','base_url','model','device','batch_size','timeout','retries','temperature','max_tokens','max_new_tokens','min_font_size','prompt','request_format','image_field','image_encoding','auth_mode','api_key_header','response_text_path','concurrency_mode','max_concurrency','requests_per_minute']) {
      if (!data.has(key)) continue
      const value = data.get(key)
      if (typeof value === 'string' && value.trim()) config[key] = ['batch_size','timeout','retries','temperature','max_tokens','max_new_tokens','min_font_size','max_concurrency','requests_per_minute'].includes(key) ? Number(value) : value.trim()
      else delete config[key]
    }
    const modelName = String(useTranslation ? translation?.config.model || '' : data.get('model') || '').trim()
    if (cloud && !modelName) {
      setSubmitError('请先获取并选择模型。')
      return
    }
    if (cloud) delete config.device
    if (useTranslation) for (const key of ['base_url', 'model', 'api_protocol']) delete config[key]
    const generatedName = cloud && modelName ? `${cloudOcr ? '多模态识字' : '云端翻译'} · ${modelName}` : existing?.name || preset.title
    setSubmitting(true); setSubmitError('')
    try {
      await onSave({kind, provider, name: generatedName, api_key: useTranslation ? undefined : String(data.get('api_key') || '').trim() || undefined, reuse_translation: useTranslation, enabled: true, is_default: true, config})
      setApiKey('')
    } catch (reason) {
      setSubmitError(reason instanceof Error ? reason.message : String(reason))
    } finally {setSubmitting(false)}
  }
  return <form className="settings-form" onSubmit={submit}>
    {submitError && <p className="settings-error" role="alert">{submitError}</p>}
    {!selected && <p className="settings-error" role="alert">当前阶段没有可用模型，请先安装推荐配置。</p>}
    <SettingsSection title={kind === 'translation' ? '连接配置' : kind === 'ocr' ? '识别模型' : '模型与设备'}>
      <SettingsField label={kind === 'translation' ? '服务商' : '当前模型'}><SelectControl ariaLabel="当前模型" value={provider} options={choices.map(item => ({value:item.name, label:providerLabel(kind,item.name)}))} onChange={next => {setProvider(next); setReuseTranslation(next === 'cloud-multimodal' && !configured.some(item => item.kind === kind && item.provider === next) && canReuseTranslation)}}/></SettingsField>
      {!cloud && kind !== 'translation' && <SettingsField label="运行设备">{kind === 'rendering' ? <span className="settings-readonly">CPU</span> : <SelectControl name="device" ariaLabel="运行设备" value={device} options={deviceOptions(selected)} onChange={setDevice}/>}</SettingsField>}
      {selected?.supports_batch && kind !== 'translation' && <SettingsField label="批大小"><NumberControl key={provider+'-batch'} ariaLabel="Batch Size" name="batch_size" min={1} step={1} defaultValue={String(defaults.batch_size ?? 4)}/></SettingsField>}
      {kind === 'rendering' && <SettingsField label="最小字号"><NumberControl key={provider+'-font'} ariaLabel="最小字号" name="min_font_size" min={1} max={512} step={1} defaultValue={String(defaults.min_font_size ?? 10)}/></SettingsField>}
      {keylessTranslation && <p className="settings-privacy">免登录、免密钥、免填模型。仅将原文发送到 Google，不上传图片或项目上下文；网页接口可能限流或不可用。</p>}
    </SettingsSection>
    {koharuOcr && <SettingsSection title="本地识别参数"><div className="settings-grid" key={provider}>
      <label>最大输出 Token 数<NumberControl ariaLabel="本地 OCR 最大输出 Token 数" name="max_new_tokens" min={16} max={2048} step={16} defaultValue={String(defaults.max_new_tokens ?? 2048)}/></label>
      <label>单区域超时（秒）<NumberControl ariaLabel="本地 OCR 超时" name="timeout" min={10} max={600} step={10} defaultValue={String(defaults.timeout ?? 180)}/></label>
    </div></SettingsSection>}
    {kind === 'detection' && provider === 'rfdetr' && <>
      <SettingsSection title="检测参数"><div className="settings-grid">{([['text_threshold','文字检测置信度',0.25],['bubble_threshold','气泡检测置信度',0.5],['panel_threshold','分镜检测置信度',0.5],['containment_threshold','文字气泡包含率',0.9]] as const).map(([name,label,fallback]) => <label key={provider+name}>{label}<NumberControl name={name} ariaLabel={label} min={name==='containment_threshold'?0.5:0.2} max={1} step={0.05} defaultValue={String(defaults[name] ?? fallback)}/></label>)}</div></SettingsSection>
      <SettingsSection title="文本框扩边"><div className="settings-padding-grid">{TEXT_BOX_PADDING_SIDES.map(([side,label]) => <label key={provider+side}>{label}（px）<NumberControl name={'text_box_padding_'+side} ariaLabel={'文本框'+label+'扩边像素'} min={0} max={50} step={1} defaultValue={paddingValue(defaults,side)}/></label>)}</div></SettingsSection>
    </>}
    {cloud && <div key={provider}>
      {cloudOcr && <SettingsSection title="连接配置">
        <p className="settings-privacy">图片会发送到你配置的服务，可能产生费用；请选择支持图片输入的模型。</p>
        <SettingsField label="连接配置"><SelectControl ariaLabel="连接配置" value={useTranslation ? 'translation' : 'custom'} options={[{value:'translation', label:'使用当前翻译模型', disabled:!canReuseTranslation}, {value:'custom', label:'独立配置视觉模型'}]} onChange={value => {setReuseTranslation(value === 'translation'); invalidateDiscovery()}}/></SettingsField>
        {useTranslation && <p className="settings-readonly">{String(translation?.config.model)} · {String(translation?.config.base_url)}</p>}
      </SettingsSection>}
      {!useTranslation && <div className="settings-connection">
        <SettingsField label="接口协议"><SelectControl name="api_protocol" ariaLabel="API 协议" value={apiProtocol} options={TRANSLATION_PROTOCOLS} onChange={next => {setApiProtocol(next); invalidateDiscovery()}}/></SettingsField>
        <SettingsField label="API 地址"><input className={inputClass} name="base_url" aria-label="API Base URL" type="url" required value={baseUrl} onChange={event => {setBaseUrl(event.target.value); invalidateDiscovery()}} placeholder="https://api.example.com/v1"/></SettingsField>
        <SettingsField label="API 密钥"><ApiKeyInput name="api_key" aria-label="API Key" hasSavedKey={savedKeyUsable} required={!savedKeyUsable} value={apiKey} onChange={event => {setApiKey(event.target.value); invalidateDiscovery()}}/></SettingsField>
        <SettingsField label="模型名称"><div className="settings-model-picker">
          <div className="settings-model-fetch">
            <SelectControl name="model" ariaLabel={cloudOcr ? '选择 OCR 模型' : '选择翻译模型'} value={model} disabled={discovering || !modelOptions.length} placeholder={discovering ? '正在获取模型…' : '请选择模型'} options={Array.from(new Set([...modelOptions,...(model ? [model] : [])])).map(item => ({value:item,label:item}))} onChange={setModel}/>
            <button className={buttonClass} type="button" aria-label="重新获取模型列表" disabled={discovering || !baseUrl.trim() || (!apiKey.trim() && !savedKeyUsable)} onClick={() => void discoverModels()}>{discovering ? <ButtonLoading label="获取中…"/> : <><RefreshCw size={16}/>获取模型</>}</button>
          </div>
          {discoveryError && <p role="alert" className="settings-error">{discoveryError}</p>}
          {discoveredEndpoint && !discoveryError && <p role="status" className="settings-notice">已获取 {modelOptions.length} 个模型</p>}
        </div></SettingsField>
      </div>}
      <details className="settings-advanced" open={cloudOcr ? true : undefined}>
        <summary><ChevronRight size={16}/><span>高级选项</span></summary>
        <div className="settings-section-body">
          <div className="settings-grid settings-grid-three">
            <label>超时（秒）<NumberControl ariaLabel="接口超时" name="timeout" min={10} max={cloudOcr ? 600 : undefined} step={5} defaultValue={String(defaults.timeout ?? 90)}/></label>
            <label>失败重试<NumberControl ariaLabel="失败重试次数" name="retries" min={0} max={5} step={1} defaultValue={String(defaults.retries ?? 2)}/></label>
            <label>Temperature<NumberControl ariaLabel="Temperature" name="temperature" min={0} max={cloudOcr ? 1 : 2} step={0.1} placeholder={cloudOcr ? '模型默认' : undefined} defaultValue={String(defaults.temperature ?? (cloudOcr ? '' : 0.2))}/></label>
          </div>
          <SettingsField label="最大输出 Token 数"><NumberControl ariaLabel="最大输出 Token 数" name="max_tokens" min={128} max={32768} step={128} defaultValue={String(defaults.max_tokens ?? 2048)}/></SettingsField>
          <label className="settings-prompt">{cloudOcr ? '识别 Prompt' : '翻译 Prompt'}<textarea aria-label={cloudOcr ? '识别 Prompt' : '翻译 Prompt'} className={textareaClass} name="prompt" defaultValue={cloudOcr ? String(defaults.prompt || DEFAULT_OCR_PROMPT) : translationPrompt(defaults.prompt)}/></label>
        </div>
      </details>
    </div>}
    {cloudOcr && <div className="settings-generic"><OCRConcurrencyFields key={provider+'-concurrency'} defaults={defaults}/></div>}
    <footer className="settings-actions"><button className={cn(buttonClass,'settings-primary')} disabled={submitting || !selected} type="submit">{submitting ? <ButtonLoading label="正在保存…"/> : '保存配置'}</button></footer>
  </form>
}
const KIND_LABELS: Record<string, string> = {detection: '文字检测', ocr: 'OCR 识别', translation: '云端翻译', inpainting: '图像修复', rendering: '排版渲染'}


const TRANSLATION_PROTOCOLS = [
  {value: 'auto', label: '自动选择'},
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
  'ocr:paddleocr-vl-1.6': {label:'PaddleOCR-VL 1.6', title:'PaddleOCR-VL 1.6（Koharu 默认）', config:{device:'auto',max_new_tokens:2048,timeout:180}},
  'ocr:baberu-ocr': {label:'Baberu OCR', title:'Baberu OCR', config:{device:'auto',max_new_tokens:2048,timeout:180}},
  'ocr:hayai-ocr': {label:'Hayai OCR', title:'Hayai OCR', config:{device:'auto',max_new_tokens:2048,timeout:180}},
  'detection:rfdetr': {label:'RF-DETR 漫画版面检测', title:'推荐漫画版面检测（RF-DETR）', config:{device:'auto',text_threshold:0.25,bubble_threshold:0.5,panel_threshold:0.5,containment_threshold:0.9,text_box_padding:{top:3,bottom:3,left:3,right:3}}},
  'detection:opencv-fallback': {label: 'OpenCV 文本检测', title: '本地 OpenCV 文本检测', config: {device: 'cpu'}},
  'ocr:manga-ocr': {label: 'MangaOCR 漫画识别', title: '推荐漫画识别（MangaOCR）', config: {device: 'auto', model: 'kha-white/manga-ocr-base'}},
  'ocr:cloud-multimodal': {label: '多模态大模型识字', title: '多模态大模型识字', config: {api_protocol: 'auto', timeout: 90, retries: 2, max_tokens: 2048, prompt: DEFAULT_OCR_PROMPT}},
  'ocr:tesseract': {label: 'Tesseract OCR', title: 'Tesseract OCR', config: {device: 'cpu'}},
  'ocr:review-fallback': {label: '人工审核占位', title: '人工审核占位 OCR', config: {}},
  'translation:openai-compatible': {label: '自定义云端翻译', title: '自定义云端翻译', config: {api_protocol: 'auto', timeout: 90, retries: 2, temperature: 0.2, max_tokens: 2048, prompt: DEFAULT_TRANSLATION_PROMPT}},
  'translation:google-web': {label:'Google 网页翻译（免配置）', title:'Google 网页翻译（免配置）', config:{timeout:30}},
  'inpainting:lama': {label: 'LaMa 复杂背景修复', title: '推荐复杂修复（LaMa）', config: {device: 'auto'}},
  'inpainting:hybrid': {label: 'LaMa 兼容修复', title: 'LaMa 兼容修复', config: {device: 'auto'}},
  'inpainting:opencv': {label: 'OpenCV 快速修复', title: '默认图像修复（OpenCV）', config: {device: 'cpu'}},
  'rendering:pillow': {label: 'Pillow 排版渲染', title: '默认排版渲染（Pillow）', config: {min_font_size: 10}},
}

function kindLabel(kind: string): string { return KIND_LABELS[kind] || kind }
function isSelectableProvider(kind: string, provider: string): boolean {
  return !(kind === 'translation' && provider === 'passthrough')
    && !(kind === 'ocr' && (provider === 'paddleocr' || provider === 'cloud-ocr'))
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
    ? [{value:'auto', label:'自动（GPU 优先）'}, {value:'cuda:0', label:'GPU（CUDA 0）'}, {value:'cpu', label:'CPU'}]
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
