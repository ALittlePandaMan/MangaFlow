import { Check, ChevronRight, CircleAlert, Cpu, Download, KeyRound, Pencil, RefreshCw, Server, Sparkles, Trash2, Type, Upload } from 'lucide-react'
import { FormEvent, useEffect, useRef, useState } from 'react'
import {NumberControl, SelectControl} from '../components/FormControls'
import { useGlobalDialog } from '../components/GlobalDialog'
import {ButtonLoading, IndeterminateProgress, SettingsSkeleton, useMinimumLoadingTime} from '../components/LoadingUI'
import { DEFAULT_FONT_FAMILIES } from '../constants/fonts'
import { api } from '../services/api'
import type { DeviceProfile, FontResource, ModelBootstrapEntry, ModelConfiguration, ModelDescriptor } from '../types'
import {buttonClass, cn, dangerButtonClass, eyebrowClass, iconButtonClass, inputClass, pageClass, primaryButtonClass, textareaClass} from '../ui'

export function SettingsPage() {
  const [available, setAvailable] = useState<ModelDescriptor[]>([])
  const [configured, setConfigured] = useState<ModelConfiguration[]>([])
  const [fonts, setFonts] = useState<FontResource[]>([])
  const [loading, setLoading] = useState(true)
  const [installing, setInstalling] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const showingInitialLoading = useMinimumLoadingTime(loading, 500)
  const {openDialog} = useGlobalDialog()
  const refreshModels = () => api.models.list().then(result => {setAvailable(result.available); setConfigured(result.configured)})
  const refreshFonts = () => api.fonts.list().then(setFonts)
  const showModelDialog = (kind: string, initial?: ModelConfiguration) => {
    openDialog({
      title: `编辑${kindLabel(kind)}配置`,
      description: '切换当前阶段使用的 Provider、设备或运行参数，保存后立即设为生效配置。',
      size:'large',
      content:({close}) => <ModelForm available={available} fixedKind={kind} initial={initial} onCancel={close} onSave={async payload => {
        if (initial) await api.models.update(initial.id, payload)
        else await api.models.create(payload)
        await refreshModels()
        setNotice(`${kindLabel(kind)}配置已更新。`)
        setError('')
      }}/>,
    })
  }
  const showFontDialog = () => {
    openDialog({
      title:'字体配置',
      description:'查看内置字体，并在这里统一添加或删除项目可用的自定义字体。',
      size:'large',
      content:({close}) => <FontManager initialFonts={fonts} onCancel={close} onChanged={async message => {
        await refreshFonts()
        setNotice(message)
        setError('')
      }}/>,
    })
  }
  const installRecommended = async (selections: RecommendationSelection[], preload: boolean): Promise<ModelBootstrapEntry[]> => {
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
      description:'逐项比较模型的效果、代价与运行要求；你可以手动选择，也可以先检测当前设备生成建议。',
      size:'xlarge',
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
    void Promise.all([refreshModels(), refreshFonts()])
      .catch(reason => setError(reason instanceof Error ? reason.message : String(reason)))
      .finally(() => setLoading(false))
  }, [])
  if (showingInitialLoading) return <SettingsSkeleton/>
  const activeConfigurations = CONFIGURATION_STAGES.map(stage => ({...stage, item: configurationForStage(configured, stage.kind)}))
  const configCard = 'grid min-h-[164px] grid-cols-[42px_minmax(0,1fr)] grid-rows-[minmax(0,1fr)_auto] gap-x-3 gap-y-3 rounded-xl border border-line bg-panel p-4 transition hover:border-line-strong hover:bg-surface hover:shadow-soft'
  const configIcon = 'col-start-1 row-start-1 grid size-[38px] place-items-center rounded-lg bg-accent/15 text-accent'
  const configBody = 'col-start-2 row-start-1 min-w-0'
  const tag = 'inline-flex items-center gap-1 rounded border border-accent/30 px-2 py-1 font-mono text-[9px] leading-[normal] not-italic text-meta'
  return <section className={`${pageClass} page-content-enter`}>
    <div className="mb-8 flex items-end justify-between gap-6"><div><span className={eyebrowClass}>WORKSPACE SETTINGS</span><h1 className="mb-2 mt-2 text-[34px] leading-tight tracking-[-1.5px]">设置</h1><p className="m-0 text-sm text-muted">六个固定入口统一管理完整处理流水线与排版字体。</p></div><div className="flex items-center gap-2"><button className={buttonClass} disabled={installing} onClick={showRecommendedDialog}>{installing ? <ButtonLoading label="正在安装模型…"/> : <><Download size={18}/>安装推荐配置</>}</button></div></div>
    {error && <div className="my-3 rounded-lg bg-danger/15 px-4 py-3 text-xs text-danger-soft-ink">{error}</div>}
    {notice && <div className="my-3 rounded-lg bg-success/15 px-4 py-3 text-xs text-success-soft-ink">{notice}</div>}
    <div className="mt-8"><h2 className="mb-3 mt-0 text-base">已配置</h2><div className="grid grid-cols-3 gap-3 max-[1100px]:grid-cols-2 max-[700px]:grid-cols-1">
      {activeConfigurations.map(({kind, label, item}) => <article className={configCard} key={kind}><div className={configIcon}>{kind === 'translation' ? <Server/> : <Cpu/>}</div><div className={configBody}><span className="font-mono text-[9px] uppercase leading-[normal] text-muted">流水线配置</span><h3 className="my-1 text-sm leading-[1.35]">{label}</h3><p className="m-0 font-mono text-[10px] leading-[normal] text-muted">{item ? providerLabel(kind, item.provider) : '尚未配置，点击编辑进行设置'}</p><div className="mt-3 flex flex-wrap gap-1">{item ? <><i className={tag}><Check size={12}/>当前配置</i>{configurationDeviceTag(item, available) && <i className={tag}>{configurationDeviceTag(item, available)}</i>}{item.has_api_key && <i className={tag}><KeyRound size={12}/>密钥已保存</i>}{kind === 'translation' && !item.has_api_key && <i className={cn(tag, 'border-warning/40 text-warning')}>待填写密钥</i>}</> : <i className={cn(tag, 'border-warning/40 text-warning')}>待配置</i>}</div></div><div className="col-start-2 row-start-2 flex justify-end"><button className={iconButtonClass} title={`编辑${label}配置`} aria-label={`编辑${label}配置`} onClick={() => showModelDialog(kind, item)}><Pencil size={15}/></button></div></article>)}
      <article className={configCard}><div className={configIcon}><Type/></div><div className={configBody}><span className="font-mono text-[9px] uppercase leading-[normal] text-muted">排版资源</span><h3 className="my-1 text-sm leading-[1.35]">字体</h3><p className="m-0 font-mono text-[10px] leading-[normal] text-muted">{DEFAULT_FONT_FAMILIES.length + fonts.length} 种字体可用</p><div className="mt-3 flex flex-wrap gap-1"><i className={tag}><Check size={12}/>{DEFAULT_FONT_FAMILIES.length} 种内置</i>{fonts.length > 0 && <i className={tag}>{fonts.length} 种自定义</i>}</div></div><div className="col-start-2 row-start-2 flex justify-end"><button className={iconButtonClass} title="编辑字体配置" aria-label="编辑字体配置" onClick={showFontDialog}><Pencil size={15}/></button></div></article>
    </div></div>
  </section>
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
    {provider:'paddleocr', title:'PaddleOCR 文字检测', summary:'高精度日文文本检测，支持旋转框和不规则排版。', pros:['漫画小字与竖排召回率高', '保留四边形和旋转信息'], cons:['首次使用需加载模型', '纯 CPU 运行较慢'], requirements:['PaddleOCR / PaddlePaddle', 'GPU 建议 2 GB+ 显存；CPU 建议 4 GB+ 内存']},
    {provider:'opencv-fallback', title:'OpenCV 文本检测', summary:'基于阈值和形态学的轻量本地检测。', pros:['启动快且无需模型权重', '低配置 CPU 也能运行'], cons:['复杂背景容易漏检或误检', '竖排、倾斜和艺术字效果较弱'], requirements:['OpenCV', '任意现代 CPU']},
  ],
  inpainting: [
    {provider:'lama', title:'LaMa 复杂背景修复', summary:'使用深度模型重建文字下方的纹理和人物背景。', pros:['复杂纹理与跨区域结构恢复最好', '适合气泡外文字和人物背景'], cons:['首次需下载权重', 'CPU 可运行但耗时明显'], requirements:['PyTorch / simple-lama-inpainting', 'GPU 建议 4 GB+ 显存；CPU 建议 8 GB+ 内存']},
    {provider:'hybrid', title:'LaMa 兼容修复', summary:'兼容旧项目命名，实际仍以 LaMa 处理所有修复区域。', pros:['兼容已有 hybrid 配置', '保持复杂修复质量'], cons:['新项目没有额外收益', '资源需求与 LaMa 相同'], requirements:['PyTorch / simple-lama-inpainting', 'GPU 建议 4 GB+ 显存']},
    {provider:'opencv', title:'OpenCV 快速修复', summary:'使用 Telea/Navier-Stokes 在遮罩周围插值。', pros:['无需模型且速度快', '低内存 CPU 友好'], cons:['复杂背景容易出现色块或模糊', '大面积文字恢复能力有限'], requirements:['OpenCV', '任意现代 CPU']},
  ],
  ocr: [
    {provider:'manga-ocr', title:'MangaOCR 漫画识别', summary:'专门针对日文漫画训练的 Transformer OCR。', pros:['日文漫画和竖排文本准确率高', '对漫画字体与标点更稳'], cons:['只负责识别，不负责检测', '首次需下载模型且 CPU 较慢'], requirements:['PyTorch / Transformers', '建议 8 GB+ 内存；GPU 建议 2 GB+ 显存']},
    {provider:'paddleocr', title:'PaddleOCR 文字识别', summary:'通用日文 OCR，可与检测阶段共用 Paddle 运行时。', pros:['通用印刷体表现稳定', '支持批量与 GPU'], cons:['漫画艺术字通常弱于 MangaOCR', 'Paddle 依赖体积较大'], requirements:['PaddleOCR / PaddlePaddle', 'GPU 建议 2 GB+ 显存']},
    {provider:'tesseract', title:'Tesseract OCR', summary:'传统 CPU OCR，适合清晰规则的印刷文字。', pros:['轻量且完全离线', '无需 GPU'], cons:['漫画竖排与艺术字准确率较低', '日文语言包必须存在'], requirements:['Tesseract 与 jpn 语言包', '任意现代 CPU']},
    {provider:'review-fallback', title:'人工审核占位', summary:'不执行真实 OCR，只创建待人工填写的区域。', pros:['无依赖且不会伪造结果'], cons:['无法自动识别文本', '所有区域都需要手工录入'], requirements:['无需模型', '适合诊断或临时占位']},
  ],
  rendering: [
    {provider:'pillow', title:'Pillow 排版渲染', summary:'本地完成横排、真竖排、换列、描边和字号拟合。', pros:['稳定可控且完全离线', '支持当前编辑器的全部文字样式'], cons:['效果依赖文本框与字体设置', '不自动生成手写艺术字'], requirements:['Pillow', 'CPU 即可，至少一种 CJK 字体']},
  ],
  translation: [
    {provider:'openai-compatible', title:'自定义云端翻译', summary:'通过 OpenAI、Responses 或 Anthropic 兼容 API 调用云端模型。', pros:['可选择高质量大模型', '支持上下文、角色语气和结构化 Region ID'], cons:['需要联网并可能产生费用', '安装后仍需填写接口、密钥和模型'], requirements:['API Base URL', 'API Key 与可用模型名', '稳定网络连接']},
    {provider:'passthrough', title:'不翻译（原文直通）', summary:'保留 OCR 原文并标记为待审核，不调用云端。', pros:['无费用且不上传文本', '适合先完成检测与修复'], cons:['不会产生目标语言译文', '必须人工翻译'], requirements:['无需外部服务', '人工审核流程']},
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
  const currentTranslation = configurationForStage(configured, 'translation')
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
        detection: pick('detection', 'paddleocr', 'opencv-fallback'),
        inpainting: pick('inpainting', 'lama', 'opencv'),
        ocr: pick('ocr', 'manga-ocr', 'tesseract'),
        rendering: 'pillow',
        translation: 'openai-compatible',
        font: 'builtin',
      }
      setProviders(nextProviders)
      setDevices(current => ({
        ...current,
        detection: result.recommendation.provider_devices.detection || 'cpu',
        inpainting: nextProviders.inpainting === 'opencv' ? 'cpu' : result.recommendation.provider_devices.inpainting || 'cpu',
        ocr: nextProviders.ocr === 'tesseract' ? 'cpu' : result.recommendation.provider_devices.ocr || 'cpu',
      }))
      setDeviceReasons({
        detection: nextProviders.detection === 'paddleocr' ? '设备资源足以运行高精度检测。' : '当前资源更适合轻量 OpenCV 检测。',
        inpainting: nextProviders.inpainting === 'lama' ? '设备资源足以运行复杂背景修复。' : '内存或运行时受限，优先保证处理速度。',
        ocr: nextProviders.ocr === 'manga-ocr' ? '设备资源适合漫画专用 OCR。' : '当前资源更适合轻量 CPU OCR。',
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
      device: ['detection', 'inpainting', 'ocr'].includes(stage.kind) ? devices[stage.kind] : undefined,
      config: stage.kind === 'translation' && providers.translation === 'openai-compatible' ? {
        ...PROVIDER_PRESETS['translation:openai-compatible'].config,
        api_protocol: translationProtocol,
        base_url: translationBaseUrl.trim(),
        model: translationModel,
      } : undefined,
      api_key: stage.kind === 'translation' && translationApiKey.trim() ? translationApiKey.trim() : undefined,
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
    : '尚未检测设备；当前选择以已有配置和通用高质量方案为准。'
  return <div className="flex min-h-[520px] flex-col">
    {error && <div className="mx-5 mt-4 rounded-lg bg-danger/15 px-3 py-2.5 text-[10px] leading-relaxed text-danger-soft-ink">{error}</div>}
    <div className="mx-5 mt-5 flex items-center justify-between gap-4 rounded-xl bg-panel px-4 py-3">
      <div className="flex min-w-0 items-center gap-3"><span className="grid size-9 shrink-0 place-items-center rounded-lg bg-accent/15 text-accent"><Cpu size={18}/></span><div className="min-w-0"><strong className="block text-[11px]">设备推荐</strong><span className="mt-1 block text-[9px] leading-relaxed text-muted">{hardwareSummary}</span>{profile && <span className="mt-1 block text-[9px] leading-relaxed text-secondary">{profile.recommendation.summary}</span>}</div></div>
      <button type="button" className={cn(buttonClass, 'shrink-0')} disabled={detecting || submitting} onClick={() => void detectAndRecommend()}>{detecting ? <ButtonLoading label="正在检测…"/> : <><Sparkles size={15}/>{profile ? '重新检测并推荐' : '根据设备推荐'}</>}</button>
    </div>
    {profile && <div className="mx-5 mt-2 flex flex-wrap gap-1.5 text-[8px]"><span className={cn('rounded px-2 py-1 font-mono', profile.runtimes.torch.cuda_available ? 'bg-success/10 text-success' : 'bg-raised text-muted')}>PyTorch CUDA {profile.runtimes.torch.cuda_available ? '可用' : '不可用'}</span><span className={cn('rounded px-2 py-1 font-mono', profile.runtimes.paddle.cuda_available ? 'bg-success/10 text-success' : 'bg-raised text-muted')}>Paddle CUDA {profile.runtimes.paddle.cuda_available ? '可用' : '不可用'}</span></div>}
    <div className="grid min-h-0 flex-1 grid-cols-[156px_minmax(0,1fr)] gap-4 px-5 py-4 max-[700px]:grid-cols-1">
      <nav className="flex flex-col gap-1.5 max-[700px]:grid max-[700px]:grid-cols-3" aria-label="推荐配置阶段">
        {RECOMMENDATION_STAGES.map((stage, index) => <button key={stage.kind} type="button" className={cn('grid grid-cols-[22px_minmax(0,1fr)_auto] items-center gap-2 rounded-lg px-2.5 py-2 text-left transition', activeKind === stage.kind ? 'bg-accent/15 text-ink' : 'text-muted hover:bg-raised hover:text-secondary')} onClick={() => setActiveKind(stage.kind)}><span className={cn('grid size-[22px] place-items-center rounded-md font-mono text-[8px]', activeKind === stage.kind ? 'bg-accent text-[#07110e]' : 'bg-raised text-muted')}>{index + 1}</span><span className="min-w-0"><strong className="block truncate text-[10px]">{stage.label}</strong><small className="mt-0.5 block truncate text-[8px] font-normal text-muted">{providers[stage.kind] === 'builtin' ? `${DEFAULT_FONT_FAMILIES.length + fonts.length} 种可用` : providerLabel(stage.kind, providers[stage.kind])}</small></span><ChevronRight size={13}/></button>)}
      </nav>
      <section className="min-w-0"><div className="mb-3"><span className="font-mono text-[8px] text-accent">{String(RECOMMENDATION_STAGES.findIndex(stage => stage.kind === activeKind) + 1).padStart(2, '0')} / 06</span><h3 className="mb-1 mt-1 text-sm">{activeStage.label}</h3><p className="m-0 text-[9px] text-muted">{activeStage.hint}。请选择一个方案，选择后仍可在对应配置中修改。</p></div>
        <div className="flex flex-col gap-2.5">
          {candidates.map(candidate => {
            const selected = providers[activeKind] === candidate.provider
            const descriptor = available.find(item => item.kind === activeKind && item.name === candidate.provider)
            const supportsCuda = descriptor?.devices.includes('cuda')
            const isCurrent = configured.some(item => item.kind === activeKind && item.provider === candidate.provider && item.is_default)
            const unavailableDuringSetup = required && activeKind === 'translation' && candidate.provider === 'passthrough'
            return <article key={candidate.provider} className={cn('overflow-hidden rounded-xl border bg-panel transition', selected ? 'border-accent/60 shadow-[0_0_0_1px_rgb(12_210_168/.12)]' : 'border-line-subtle hover:border-line-strong')}>
              <button type="button" disabled={unavailableDuringSetup} className="grid w-full grid-cols-[20px_minmax(0,1fr)_auto] items-start gap-3 bg-transparent px-3.5 py-3 text-left text-inherit disabled:cursor-not-allowed disabled:opacity-45" onClick={() => setProviders(current => ({...current, [activeKind]:candidate.provider}))}>
                <span className={cn('mt-0.5 grid size-4 place-items-center rounded-full border', selected ? 'border-accent bg-accent' : 'border-line-strong bg-transparent')}>{selected && <Check size={11} className="text-[#07110e]"/>}</span>
                <span className="min-w-0"><span className="flex flex-wrap items-center gap-2"><strong className="text-[11px]">{candidate.title}</strong>{isCurrent && <i className="rounded bg-accent/10 px-1.5 py-0.5 font-mono text-[7px] not-italic text-accent">当前配置</i>}{descriptor && <i className={cn('rounded px-1.5 py-0.5 font-mono text-[7px] not-italic', descriptor.installed ? 'bg-success/10 text-success' : 'bg-warning/10 text-warning')}>{descriptor.installed ? '依赖已安装' : '缺少运行依赖'}</i>}{unavailableDuringSetup && <i className="rounded bg-danger/10 px-1.5 py-0.5 font-mono text-[7px] not-italic text-danger">首次启动不可用</i>}</span><small className="mt-1 block text-[9px] leading-relaxed text-muted">{candidate.summary}</small></span>
                <span className="font-mono text-[8px] text-muted">{descriptor?.devices.join(' / ') || 'resource'}</span>
              </button>
              {selected && <div className="border-t border-line-subtle px-3.5 py-3"><div className="grid grid-cols-3 gap-3 max-[620px]:grid-cols-1"><GuideList title="优势" items={candidate.pros} tone="success"/><GuideList title="注意" items={candidate.cons} tone="warning"/><GuideList title="运行要求" items={candidate.requirements} tone="neutral"/></div>{deviceReasons[activeKind] && <div className="mt-2.5 flex items-start gap-2 rounded-lg bg-accent/10 px-2.5 py-2 text-[8px] leading-relaxed text-accent-soft-ink"><Sparkles className="mt-px shrink-0" size={12}/><span>设备建议：{deviceReasons[activeKind]}</span></div>}{supportsCuda && activeKind !== 'font' && <div className="mt-3 grid grid-cols-[90px_minmax(0,180px)] items-center gap-2"><span className="text-[9px] text-secondary">运行设备</span><SelectControl ariaLabel={`${candidate.title}运行设备`} value={devices[activeKind] || 'cpu'} options={deviceOptions(descriptor)} onChange={value => setDevices(current => ({...current, [activeKind]:value}))}/></div>}{activeKind === 'translation' && candidate.provider === 'openai-compatible' && <div className="mt-3 grid grid-cols-2 gap-2.5 rounded-xl bg-canvas p-3"><div className="text-[9px] text-secondary"><span className="mb-1.5 block">API 协议</span><SelectControl ariaLabel="首次配置翻译 API 协议" value={translationProtocol} options={TRANSLATION_PROTOCOLS} onChange={value => {setTranslationProtocol(value); setTranslationVerified(false)}}/></div><label className="text-[9px] text-secondary">API Base URL<input className={cn(inputClass, 'mt-1.5')} value={translationBaseUrl} onChange={event => {setTranslationBaseUrl(event.target.value); setModelDiscoveryError(''); setTranslationVerified(false)}} placeholder="https://api.example.com/v1"/></label><label className="col-span-full text-[9px] text-secondary">API Key<input className={cn(inputClass, 'mt-1.5')} type="password" value={translationApiKey} onChange={event => {setTranslationApiKey(event.target.value); setModelDiscoveryError(''); setTranslationVerified(false)}} autoComplete="new-password" placeholder={currentTranslation?.has_api_key ? '密钥已保存，留空则继续使用' : '请输入云端接口密钥'}/></label><div className="col-span-full text-[9px] text-secondary"><span className="mb-1.5 block">模型</span><div className="grid grid-cols-[minmax(0,1fr)_auto] gap-2"><SelectControl ariaLabel="首次配置翻译模型" value={translationModel} disabled={discoveringModels || !translationModels.length} placeholder={discoveringModels ? '正在获取模型…' : '请先连接接口获取模型'} options={translationModels.map(item => ({value:item, label:item}))} onChange={setTranslationModel}/><button type="button" className={buttonClass} disabled={discoveringModels || !translationBaseUrl.trim()} onClick={() => void discoverTranslationModels()}>{discoveringModels ? <ButtonLoading label="连接中…"/> : <><RefreshCw size={14}/>{translationVerified ? '已通过连接检测' : '连接并获取模型'}</>}</button></div>{modelDiscoveryError && <small className="mt-2 block text-[8px] leading-relaxed text-danger">{modelDiscoveryError}</small>}</div><div className={cn('col-span-full flex items-start gap-2 rounded-lg px-2.5 py-2 text-[8px] leading-relaxed', translationVerified ? 'bg-success/10 text-success' : 'bg-warning/10 text-warning')}>{translationVerified ? <Check className="mt-px shrink-0" size={12}/> : <CircleAlert className="mt-px shrink-0" size={12}/>} {translationVerified ? '接口连接检测通过，保存时会加密写入密钥。' : '必须成功获取模型，才能确认接口地址、协议和密钥有效。'}</div></div>}{activeKind === 'font' && <div className="mt-2.5 text-[8px] text-muted">当前：{DEFAULT_FONT_FAMILIES.length} 种内置字体{fonts.length ? `，${fonts.length} 种自定义字体` : '；暂未添加自定义字体'}。</div>}</div>}
            </article>
          })}
        </div>
      </section>
    </div>
    {submitting && <div className="border-t border-line-subtle bg-canvas/35 px-5 py-3"><IndeterminateProgress label="正在安装配置、加载模型并执行可用性检测"/></div>}<footer className="sticky bottom-0 z-[2] flex items-center justify-between gap-4 border-t border-line-subtle bg-surface px-5 pb-5 pt-4"><button type="button" role="switch" aria-checked={preload} className="flex items-center gap-2 border-0 bg-transparent p-0 text-left text-inherit disabled:cursor-default" disabled={submitting || required} onClick={() => setPreload(value => !value)}><span className={cn('relative h-5 w-9 rounded-full transition', preload ? 'bg-accent' : 'bg-raised')}><i className={cn('absolute top-0.5 size-4 rounded-full bg-white transition', preload ? 'left-[18px]' : 'left-0.5')}/></span><span><strong className="block text-[9px]">立即下载并加载本地模型</strong><small className="mt-0.5 block text-[8px] text-muted">{required ? '首次启动必须加载模型并通过可用性检测' : '关闭时只保存配置，权重会在首次使用时加载'}</small></span></button><div className="flex shrink-0 gap-2">{!required && onCancel && <button type="button" className={buttonClass} disabled={submitting} onClick={onCancel}>取消</button>}<button type="button" className={primaryButtonClass} disabled={submitting || detecting || (required && !profile)} onClick={() => void submit()}>{submitting ? <ButtonLoading label="正在安装并检测…"/> : <><Download size={15}/>{required ? '安装、检测并进入工作台' : '安装所选配置'}</>}</button></div></footer>
  </div>
}

function GuideList({title, items, tone}: {title: string, items: string[], tone: 'success' | 'warning' | 'neutral'}) {
  return <div><strong className={cn('mb-1.5 block font-mono text-[8px]', tone === 'success' ? 'text-success' : tone === 'warning' ? 'text-warning' : 'text-secondary')}>{title}</strong><ul className="m-0 flex list-none flex-col gap-1 p-0">{items.map(item => <li key={item} className="flex items-start gap-1.5 text-[8px] leading-relaxed text-muted"><i className={cn('mt-[5px] size-1 shrink-0 rounded-full', tone === 'success' ? 'bg-success' : tone === 'warning' ? 'bg-warning' : 'bg-muted')}/><span>{item}</span></li>)}</ul></div>
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
    const descriptor = available.find(item => item.kind === kind && item.name === current?.provider)
    output[kind] = concreteDevice(current?.config.device, descriptor)
  }
  return output
}

function FontManager({initialFonts, onCancel, onChanged}: {initialFonts: FontResource[], onCancel: () => void, onChanged: (message: string) => Promise<void>}) {
  const [items, setItems] = useState(initialFonts)
  const [busy, setBusy] = useState('')
  const [error, setError] = useState('')
  const input = useRef<HTMLInputElement>(null)
  const upload = async (file: File) => {
    setBusy('upload'); setError('')
    try {
      const uploaded = await api.fonts.upload(file)
      setItems(await api.fonts.list())
      await onChanged(`字体“${uploaded.name}”已添加。`)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason))
    } finally {
      setBusy('')
      if (input.current) input.current.value = ''
    }
  }
  const remove = async (font: FontResource) => {
    setBusy(font.filename); setError('')
    try {
      await api.fonts.remove(font.filename)
      setItems(await api.fonts.list())
      await onChanged(`字体“${font.name}”已删除。`)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason))
    } finally {
      setBusy('')
    }
  }
  const fontCard = 'grid min-h-[84px] min-w-0 grid-cols-[38px_minmax(0,1fr)_auto] items-center gap-3 rounded-lg border border-line-subtle bg-surface p-3'
  return <div className="flex min-h-[360px] flex-col">
    {error && <div className="mx-5 mt-4 rounded-lg bg-danger/15 px-3 py-2.5 text-[10px] leading-relaxed text-danger-soft-ink">{error}</div>}
    <div className="flex items-center justify-between gap-4 px-6 pt-5"><div className="flex flex-col gap-1"><strong className="text-xs">可用字体</strong><span className="text-[9px] text-muted">{DEFAULT_FONT_FAMILIES.length} 种内置 · {items.length} 种自定义</span></div><button className={buttonClass} type="button" disabled={Boolean(busy)} onClick={() => input.current?.click()}>{busy === 'upload' ? <ButtonLoading label="正在添加…"/> : <><Upload size={15}/>添加字体</>}</button><input ref={input} hidden type="file" accept=".ttf,.otf,font/ttf,font/otf" onChange={event => {const file = event.target.files?.[0]; if (file) void upload(file)}}/></div>
    <div className="grid grid-cols-[repeat(auto-fill,minmax(285px,1fr))] gap-3 px-6 py-5">
      {DEFAULT_FONT_FAMILIES.map(font => <article className={fontCard} key={font.name}><div className="grid size-9 place-items-center rounded-lg bg-raised text-secondary"><Type size={19}/></div><div className="min-w-0"><span className="block truncate font-mono text-[8px] text-muted">内置字体</span><strong className="my-1 block truncate text-xs" style={{fontFamily:font.name}}>{font.label}</strong><small className="block truncate font-mono text-[8px] text-muted">{font.name}</small></div><i className="font-mono text-[7px] not-italic text-meta">BUILT-IN</i></article>)}
      {items.map(font => <article className={fontCard} key={font.filename}><div className="grid size-9 place-items-center rounded-lg bg-accent/15 text-accent"><Type size={19}/></div><div className="min-w-0"><span className="block truncate font-mono text-[8px] text-muted">自定义字体</span><strong className="my-1 block truncate text-xs" style={{fontFamily:font.name}}>{font.name}</strong><small className="block truncate font-mono text-[8px] text-muted">{font.filename}</small></div><button type="button" className={dangerButtonClass} disabled={Boolean(busy)} title={`删除${font.name}`} onClick={() => void remove(font)}>{busy === font.filename ? <ButtonLoading compact label={`正在删除${font.name}`}/> : <Trash2 size={15}/>}</button></article>)}
    </div>
    <footer className="mt-auto flex justify-end border-t border-line-subtle px-6 pb-5 pt-4"><button className={buttonClass} type="button" onClick={onCancel}>完成</button></footer>
  </div>
}

function ModelForm({available, fixedKind, initial, onCancel, onSave}: {available: ModelDescriptor[], fixedKind?: string, initial?: ModelConfiguration, onCancel: () => void, onSave: (payload: Record<string, unknown>) => Promise<void>}) {
  const [submitting, setSubmitting] = useState(false)
  const [submitError, setSubmitError] = useState('')
  const initialKind = initial?.kind || fixedKind || 'translation'
  const [kind, setKind] = useState(initialKind)
  const choices = available.filter(item => item.kind === kind)
  const [provider, setProvider] = useState(initial?.provider || preferredProvider(initialKind, available.filter(item => item.kind === initialKind)))
  const selected = choices.find(item => item.name === provider)
  const preset = providerPreset(kind, provider)
  const editingSameProvider = initial?.kind === kind && initial.provider === provider
  const defaults = editingSameProvider ? initial.config : preset.config
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

  useEffect(() => {
    if (kind !== 'translation') {
      setDevice(concreteDevice(defaults.device, selected))
      return
    }
    if (provider === 'passthrough') return
    const nextBaseUrl = String(defaults.base_url ?? '')
    const nextModel = String(defaults.model ?? '')
    setApiProtocol(String(defaults.api_protocol ?? 'auto'))
    setBaseUrl(nextBaseUrl)
    setModel(nextModel)
    setModelOptions(nextModel ? [nextModel] : [])
    setApiKey('')
    setDiscoveryError('')
    setDiscoveredEndpoint('')
  }, [kind, provider])

  const discoverModels = async () => {
    const normalizedUrl = baseUrl.trim()
    if (!normalizedUrl) return
    if (!/^https?:\/\//i.test(normalizedUrl)) {
      setDiscoveryError('请输入以 http:// 或 https:// 开头的 API Base URL')
      return
    }
    setDiscovering(true)
    setDiscoveryError('')
    try {
      const result = await api.models.discover({base_url: normalizedUrl, api_protocol: apiProtocol, api_key: apiKey.trim() || undefined, config_id: initial?.id})
      setModelOptions(result.models)
      setModel(current => result.models.includes(current) ? current : result.models[0] || '')
      setBaseUrl(result.base_url)
      setDiscoveredEndpoint(result.endpoint)
    } catch (reason) {
      setModelOptions(model ? [model] : [])
      setDiscoveryError(reason instanceof Error ? reason.message : String(reason))
      setDiscoveredEndpoint('')
    } finally {
      setDiscovering(false)
    }
  }
  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault(); const data = new FormData(event.currentTarget)
    const config: Record<string, unknown> = {...defaults}
    for (const key of ['api_protocol','base_url','model','device','batch_size','timeout','retries','temperature','min_font_size','prompt']) {
      if (!data.has(key)) continue
      const value = data.get(key)
      if (typeof value === 'string' && value.trim()) config[key] = ['batch_size','timeout','retries','temperature','min_font_size'].includes(key) ? Number(value) : value.trim()
      else delete config[key]
    }
    const modelName = String(data.get('model') || '').trim()
    if (kind === 'translation' && provider !== 'passthrough' && !modelName) {
      setSubmitError('请先根据 API Base URL 获取并选择一个模型。')
      return
    }
    const generatedName = kind === 'translation' && modelName ? `云端翻译 · ${modelName}` : initial?.name || preset.title
    setSubmitting(true); setSubmitError('')
    try {
      await onSave({kind, provider, name: generatedName, api_key: data.get('api_key') || undefined, enabled: true, is_default: true, config})
      onCancel()
    } catch (reason) {
      setSubmitError(reason instanceof Error ? reason.message : String(reason))
      setSubmitting(false)
    }
  }
  const fieldClass = 'min-w-0 text-[10px] text-secondary [&>*:last-child]:mt-2'
  return <form className="m-0 bg-transparent" onSubmit={submit}>{submitError && <div className="mx-6 mt-5 rounded-lg bg-danger/15 px-3 py-2.5 text-[10px] leading-relaxed text-danger-soft-ink">{submitError}</div>}<div className="grid grid-cols-2 gap-3 px-6 py-5">
    <div className={fieldClass}><span>流水线阶段</span><SelectControl ariaLabel="流水线阶段" value={kind} disabled={Boolean(initial || fixedKind)} options={[{value:'detection', label:'文字检测'}, {value:'ocr', label:'OCR'}, {value:'translation', label:'翻译'}, {value:'inpainting', label:'图像修复'}, {value:'rendering', label:'排版渲染'}]} onChange={nextKind => {setKind(nextKind); setProvider(preferredProvider(nextKind, available.filter(item => item.kind === nextKind)))}}/></div>
    {kind === 'translation' ? <div className={fieldClass}><span>API 协议</span><SelectControl name="api_protocol" ariaLabel="API 协议" value={apiProtocol} options={TRANSLATION_PROTOCOLS} onChange={nextProtocol => {setApiProtocol(nextProtocol); setDiscoveryError(''); setDiscoveredEndpoint('')}}/></div> : <div className={fieldClass}><span>Provider 类型</span><SelectControl ariaLabel="Provider 类型" value={provider} options={choices.map(item => ({value:item.name, label:`${providerLabel(kind, item.name)}${item.is_fallback ? '（备用）' : ''}`}))} onChange={setProvider}/></div>}
    {kind !== 'translation' && <div className="col-span-full grid grid-cols-[auto_minmax(0,1fr)_auto] items-center gap-3 rounded-lg bg-surface p-3"><strong className="text-[11px] text-ink">{providerLabel(kind, provider)}</strong><span className="text-[10px] text-muted">{selected?.description}</span><i className="font-mono text-[9px] not-italic text-accent">{selected?.devices.join(' / ')}</i></div>}
    {kind !== 'translation' && kind !== 'rendering' && <div className={fieldClass} key={`${kind}-${provider}-device`}><span>运行设备</span><SelectControl name="device" ariaLabel="运行设备" value={device} options={deviceOptions(selected)} onChange={setDevice}/></div>}
    {selected?.supports_batch && kind !== 'translation' && <label className={fieldClass} key={`${kind}-${provider}-batch`}>Batch Size<NumberControl ariaLabel="Batch Size" name="batch_size" min={1} step={1} defaultValue={String(defaults.batch_size ?? '')} placeholder="4"/></label>}
    {kind === 'translation' && provider !== 'passthrough' && <div className="col-span-full grid grid-cols-2 gap-3" key={`${kind}-${provider}`}>
      <label className={cn(fieldClass, 'col-span-full')}>API Base URL<input className={inputClass} name="base_url" required value={baseUrl} onChange={event => {setBaseUrl(event.target.value); setDiscoveryError(''); setDiscoveredEndpoint('')}} onBlur={() => void discoverModels()} placeholder="https://api.example.com/v1"/></label>
      <label className={cn(fieldClass, 'col-span-full')}>API Key<input className={inputClass} name="api_key" type="password" required={!(initial?.has_api_key && editingSameProvider)} value={apiKey} onChange={event => {setApiKey(event.target.value); setDiscoveryError('')}} onBlur={() => void discoverModels()} autoComplete="new-password" placeholder={initial?.has_api_key && editingSameProvider ? '密钥已保存，留空则保持不变' : '输入密钥后将自动获取模型列表'}/></label>
      <div className={cn(fieldClass, 'col-span-full')}><span>模型</span><div className="mt-2 grid grid-cols-[minmax(0,1fr)_auto] gap-2"><SelectControl name="model" ariaLabel="选择翻译模型" value={model} disabled={discovering || !modelOptions.length} placeholder={discovering ? '正在获取模型…' : '请先填写接口地址和密钥'} options={modelOptions.map(item => ({value:item, label:item}))} onChange={setModel}/><button className={buttonClass} type="button" title="重新获取模型列表" aria-label="重新获取模型列表" disabled={discovering || !baseUrl.trim()} onClick={() => void discoverModels()}>{discovering ? <ButtonLoading label="获取中…"/> : <><RefreshCw size={15}/><span>获取模型</span></>}</button></div>{discoveryError ? <small className="mt-2 block text-[9px] text-danger">{discoveryError}</small> : discoveredEndpoint && <small className="mt-2 block font-mono text-[9px] text-muted">已从 {discoveredEndpoint} 获取 {modelOptions.length} 个模型</small>}</div>
      <div className="col-span-full grid grid-cols-3 gap-3">
        <label className={fieldClass}>超时（秒）<NumberControl ariaLabel="接口超时" name="timeout" min={10} step={5} defaultValue={String(defaults.timeout ?? 90)}/></label>
        <label className={fieldClass}>失败重试<NumberControl ariaLabel="失败重试次数" name="retries" min={0} max={5} step={1} defaultValue={String(defaults.retries ?? 2)}/></label>
        <label className={fieldClass}>Temperature<NumberControl ariaLabel="Temperature" name="temperature" min={0} max={2} step={0.1} defaultValue={String(defaults.temperature ?? 0.2)}/></label>
      </div>
      <label className={cn(fieldClass, 'col-span-full')}>翻译 Prompt<textarea className={cn(textareaClass, 'min-h-[220px] resize-y overflow-y-auto leading-relaxed')} name="prompt" defaultValue={translationPrompt(defaults.prompt)} placeholder="漫画语气、角色称谓等翻译要求"/></label>
    </div>}
    {kind === 'rendering' && <label className={fieldClass} key={`${kind}-${provider}-font-size`}>最小字号<NumberControl ariaLabel="最小字号" name="min_font_size" min={1} step={1} defaultValue={String(defaults.min_font_size ?? 10)}/></label>}
  </div><footer className="sticky bottom-0 z-[2] flex justify-end gap-2 border-t border-line-subtle bg-[linear-gradient(180deg,rgb(29_32_28/.94),var(--color-surface)_28%)] px-6 pb-5 pt-4 [&_button]:min-w-[88px]"><button className={buttonClass} type="button" disabled={submitting} onClick={onCancel}>取消</button><button className={primaryButtonClass} disabled={submitting} type="submit">{submitting ? <ButtonLoading label="正在保存…"/> : '保存配置'}</button></footer></form>
}

const KIND_LABELS: Record<string, string> = {detection: '文字检测', ocr: 'OCR 识别', translation: '云端翻译', inpainting: '图像修复', rendering: '排版渲染'}

const CONFIGURATION_STAGES = [
  {kind:'detection', label:'文字检测'},
  {kind:'inpainting', label:'图像修复'},
  {kind:'ocr', label:'OCR 识别'},
  {kind:'rendering', label:'排版渲染'},
  {kind:'translation', label:'云端翻译'},
]

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
  'detection:paddleocr': {label: 'PaddleOCR 文字检测', title: '推荐文字检测（PaddleOCR）', config: {device: 'cuda:0', language: 'japan', ocr_version: 'PP-OCRv5', box_threshold: 0.45, unclip_ratio: 1.8, group_text_lines: false}},
  'detection:opencv-fallback': {label: 'OpenCV 文本检测', title: '本地 OpenCV 文本检测', config: {device: 'cpu'}},
  'ocr:manga-ocr': {label: 'MangaOCR 漫画识别', title: '推荐漫画识别（MangaOCR）', config: {device: 'cuda:0', model: 'kha-white/manga-ocr-base'}},
  'ocr:paddleocr': {label: 'PaddleOCR 文字识别', title: 'PaddleOCR 文字识别', config: {device: 'cuda:0', language: 'japan'}},
  'ocr:tesseract': {label: 'Tesseract OCR', title: 'Tesseract OCR', config: {device: 'cpu'}},
  'ocr:review-fallback': {label: '人工审核占位', title: '人工审核占位 OCR', config: {}},
  'translation:openai-compatible': {label: '自定义云端翻译', title: '自定义云端翻译', config: {api_protocol: 'auto', timeout: 90, retries: 2, temperature: 0.2, prompt: DEFAULT_TRANSLATION_PROMPT}},
  'translation:passthrough': {label: '不翻译（原文直通）', title: '安全翻译占位', config: {review_required: true}},
  'inpainting:lama': {label: 'LaMa 复杂背景修复', title: '推荐复杂修复（LaMa）', config: {device: 'cuda:0'}},
  'inpainting:hybrid': {label: 'LaMa 兼容修复', title: 'LaMa 兼容修复', config: {device: 'cuda:0'}},
  'inpainting:opencv': {label: 'OpenCV 快速修复', title: '默认图像修复（OpenCV）', config: {device: 'cpu'}},
  'rendering:pillow': {label: 'Pillow 排版渲染', title: '默认排版渲染（Pillow）', config: {min_font_size: 10}},
}

function kindLabel(kind: string): string { return KIND_LABELS[kind] || kind }
function configurationForStage(configured: ModelConfiguration[], kind: string): ModelConfiguration | undefined {
  const candidates = configured.filter(item => item.kind === kind)
  if (kind === 'translation') {
    return candidates.find(item => item.provider === 'openai-compatible' && item.is_default)
      || candidates.find(item => item.provider === 'openai-compatible' && item.enabled)
      || candidates.find(item => item.provider === 'openai-compatible')
  }
  return candidates.find(item => item.enabled && item.is_default)
    || candidates.find(item => item.is_default)
    || candidates.find(item => item.enabled)
    || candidates[0]
}
function configurationDeviceTag(item: ModelConfiguration, available: ModelDescriptor[]): string {
  if (item.kind === 'translation') return '云端 API'
  const descriptor = available.find(entry => entry.kind === item.kind && entry.name === item.provider)
  const value = String(item.config.device || '').toLowerCase()
  if (value === 'cpu' || (!descriptor?.devices.includes('cuda') && descriptor?.devices.includes('cpu'))) return 'CPU'
  if (value.startsWith('cuda') || value.startsWith('gpu') || descriptor?.devices.includes('cuda')) return 'GPU'
  return ''
}
function concreteDevice(value: unknown, selected?: ModelDescriptor): string {
  const configured = String(value || '').trim().toLowerCase()
  if (configured === 'cpu' || !selected?.devices.includes('cuda')) return 'cpu'
  if (configured.startsWith('cuda') || configured.startsWith('gpu')) return 'cuda:0'
  return 'cuda:0'
}
function deviceOptions(selected?: ModelDescriptor) {
  return selected?.devices.includes('cuda')
    ? [{value:'cuda:0', label:'GPU（CUDA 0）'}, {value:'cpu', label:'CPU'}]
    : [{value:'cpu', label:'CPU'}]
}
function providerPreset(kind: string, provider: string) { return PROVIDER_PRESETS[`${kind}:${provider}`] || {label: provider, title: provider, config: {}} }
function providerLabel(kind: string, provider: string): string { return providerPreset(kind, provider).label }
function translationPrompt(value: unknown): string {
  const prompt = typeof value === 'string' ? value.trim() : ''
  return !prompt || prompt === LEGACY_TRANSLATION_PROMPT ? DEFAULT_TRANSLATION_PROMPT : prompt
}
function preferredProvider(kind: string, choices: ModelDescriptor[]): string {
  const preferred: Record<string, string> = {detection: 'paddleocr', ocr: 'manga-ocr', translation: 'openai-compatible', inpainting: 'lama', rendering: 'pillow'}
  return choices.some(item => item.name === preferred[kind]) ? preferred[kind] : choices[0]?.name || ''
}
