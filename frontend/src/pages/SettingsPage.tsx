import { Check, Cpu, Download, KeyRound, LoaderCircle, Pencil, RefreshCw, Server, Trash2, Type, Upload } from 'lucide-react'
import { FormEvent, useEffect, useRef, useState } from 'react'
import { Loading } from '../components/AppShell'
import {NumberControl, SelectControl} from '../components/FormControls'
import { useGlobalDialog } from '../components/GlobalDialog'
import { DEFAULT_FONT_FAMILIES } from '../constants/fonts'
import { api } from '../services/api'
import type { FontResource, ModelConfiguration, ModelDescriptor } from '../types'
import {buttonClass, cn, dangerButtonClass, eyebrowClass, iconButtonClass, inputClass, pageClass, primaryButtonClass, textareaClass} from '../ui'

export function SettingsPage() {
  const [available, setAvailable] = useState<ModelDescriptor[]>([])
  const [configured, setConfigured] = useState<ModelConfiguration[]>([])
  const [fonts, setFonts] = useState<FontResource[]>([])
  const [loading, setLoading] = useState(true)
  const [installing, setInstalling] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
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
  const installRecommended = async () => {
    setInstalling(true)
    setError('')
    setNotice('')
    try {
      const result = await api.models.bootstrap()
      const ready = result.models.filter(item => item.status === 'ready').length
      if (!result.ok) setError(result.models.filter(item => item.error).map(item => `${item.kind}: ${item.error}`).join('；'))
      else setNotice(`推荐配置与模型已就绪（${ready}/${result.models.length}）`)
      await refreshModels()
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason))
    } finally {
      setInstalling(false)
    }
  }
  useEffect(() => {
    void Promise.all([refreshModels(), refreshFonts()])
      .catch(reason => setError(reason instanceof Error ? reason.message : String(reason)))
      .finally(() => setLoading(false))
  }, [])
  if (loading) return <Loading label="正在读取设置…"/>
  const activeConfigurations = CONFIGURATION_STAGES.map(stage => ({...stage, item: configurationForStage(configured, stage.kind)}))
  const configCard = 'grid min-h-[164px] grid-cols-[42px_minmax(0,1fr)] grid-rows-[minmax(0,1fr)_auto] gap-x-3 gap-y-3 rounded-xl border border-line bg-panel p-4 transition hover:border-line-strong hover:bg-surface hover:shadow-soft'
  const configIcon = 'col-start-1 row-start-1 grid size-[38px] place-items-center rounded-lg bg-accent/15 text-accent'
  const configBody = 'col-start-2 row-start-1 min-w-0'
  const tag = 'inline-flex items-center gap-1 rounded border border-accent/30 px-2 py-1 font-mono text-[9px] leading-[normal] not-italic text-[#9acabb]'
  return <section className={pageClass}>
    <div className="mb-8 flex items-end justify-between gap-6"><div><span className={eyebrowClass}>WORKSPACE SETTINGS</span><h1 className="mb-2 mt-2 text-[34px] leading-tight tracking-[-1.5px]">设置</h1><p className="m-0 text-sm text-muted">六个固定入口统一管理完整处理流水线与排版字体。</p></div><div className="flex items-center gap-2"><button className={buttonClass} disabled={installing} onClick={() => void installRecommended()}><Download size={18}/>{installing ? '正在安装模型…' : '安装推荐配置'}</button></div></div>
    {error && <div className="my-3 rounded-lg bg-danger/15 px-4 py-3 text-xs text-[#ffb0a9]">{error}</div>}
    {notice && <div className="my-3 rounded-lg bg-success/15 px-4 py-3 text-xs text-[#9de7d2]">{notice}</div>}
    <div className="mt-8"><h2 className="mb-3 mt-0 text-base">已配置</h2><div className="grid grid-cols-3 gap-3 max-[1100px]:grid-cols-2 max-[700px]:grid-cols-1">
      {activeConfigurations.map(({kind, label, item}) => <article className={configCard} key={kind}><div className={configIcon}>{kind === 'translation' ? <Server/> : <Cpu/>}</div><div className={configBody}><span className="font-mono text-[9px] uppercase leading-[normal] text-muted">流水线配置</span><h3 className="my-1 text-sm leading-[1.35]">{label}</h3><p className="m-0 font-mono text-[10px] leading-[normal] text-muted">{item ? providerLabel(kind, item.provider) : '尚未配置，点击编辑进行设置'}</p><div className="mt-3 flex flex-wrap gap-1">{item ? <><i className={tag}><Check size={12}/>当前配置</i>{configurationDeviceTag(item, available) && <i className={tag}>{configurationDeviceTag(item, available)}</i>}{item.has_api_key && <i className={tag}><KeyRound size={12}/>密钥已保存</i>}{kind === 'translation' && !item.has_api_key && <i className={cn(tag, 'border-warning/40 text-warning')}>待填写密钥</i>}</> : <i className={cn(tag, 'border-warning/40 text-warning')}>待配置</i>}</div></div><div className="col-start-2 row-start-2 flex justify-end"><button className={iconButtonClass} title={`编辑${label}配置`} aria-label={`编辑${label}配置`} onClick={() => showModelDialog(kind, item)}><Pencil size={15}/></button></div></article>)}
      <article className={configCard}><div className={configIcon}><Type/></div><div className={configBody}><span className="font-mono text-[9px] uppercase leading-[normal] text-muted">排版资源</span><h3 className="my-1 text-sm leading-[1.35]">字体</h3><p className="m-0 font-mono text-[10px] leading-[normal] text-muted">{DEFAULT_FONT_FAMILIES.length + fonts.length} 种字体可用</p><div className="mt-3 flex flex-wrap gap-1"><i className={tag}><Check size={12}/>{DEFAULT_FONT_FAMILIES.length} 种内置</i>{fonts.length > 0 && <i className={tag}>{fonts.length} 种自定义</i>}</div></div><div className="col-start-2 row-start-2 flex justify-end"><button className={iconButtonClass} title="编辑字体配置" aria-label="编辑字体配置" onClick={showFontDialog}><Pencil size={15}/></button></div></article>
    </div></div>
  </section>
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
    {error && <div className="mx-5 mt-4 rounded-lg bg-danger/15 px-3 py-2.5 text-[10px] leading-relaxed text-[#ffb7b1]">{error}</div>}
    <div className="flex items-center justify-between gap-4 px-6 pt-5"><div className="flex flex-col gap-1"><strong className="text-xs">可用字体</strong><span className="text-[9px] text-muted">{DEFAULT_FONT_FAMILIES.length} 种内置 · {items.length} 种自定义</span></div><button className={buttonClass} type="button" disabled={Boolean(busy)} onClick={() => input.current?.click()}><Upload size={15}/>{busy === 'upload' ? '正在添加…' : '添加字体'}</button><input ref={input} hidden type="file" accept=".ttf,.otf,font/ttf,font/otf" onChange={event => {const file = event.target.files?.[0]; if (file) void upload(file)}}/></div>
    <div className="grid grid-cols-[repeat(auto-fill,minmax(285px,1fr))] gap-3 px-6 py-5">
      {DEFAULT_FONT_FAMILIES.map(font => <article className={fontCard} key={font.name}><div className="grid size-9 place-items-center rounded-lg bg-raised text-secondary"><Type size={19}/></div><div className="min-w-0"><span className="block truncate font-mono text-[8px] text-muted">内置字体</span><strong className="my-1 block truncate text-xs" style={{fontFamily:font.name}}>{font.label}</strong><small className="block truncate font-mono text-[8px] text-muted">{font.name}</small></div><i className="font-mono text-[7px] not-italic text-[#7e9b91]">BUILT-IN</i></article>)}
      {items.map(font => <article className={fontCard} key={font.filename}><div className="grid size-9 place-items-center rounded-lg bg-accent/15 text-accent"><Type size={19}/></div><div className="min-w-0"><span className="block truncate font-mono text-[8px] text-muted">自定义字体</span><strong className="my-1 block truncate text-xs" style={{fontFamily:font.name}}>{font.name}</strong><small className="block truncate font-mono text-[8px] text-muted">{font.filename}</small></div><button type="button" className={dangerButtonClass} disabled={Boolean(busy)} title={`删除${font.name}`} onClick={() => void remove(font)}>{busy === font.filename ? <LoaderCircle className="animate-spin" size={15}/> : <Trash2 size={15}/>}</button></article>)}
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
  return <form className="m-0 bg-transparent" onSubmit={submit}>{submitError && <div className="mx-6 mt-5 rounded-lg bg-danger/15 px-3 py-2.5 text-[10px] leading-relaxed text-[#ffb7b1]">{submitError}</div>}<div className="grid grid-cols-2 gap-3 px-6 py-5">
    <div className={fieldClass}><span>流水线阶段</span><SelectControl ariaLabel="流水线阶段" value={kind} disabled={Boolean(initial || fixedKind)} options={[{value:'detection', label:'文字检测'}, {value:'ocr', label:'OCR'}, {value:'translation', label:'翻译'}, {value:'inpainting', label:'图像修复'}, {value:'rendering', label:'排版渲染'}]} onChange={nextKind => {setKind(nextKind); setProvider(preferredProvider(nextKind, available.filter(item => item.kind === nextKind)))}}/></div>
    {kind === 'translation' ? <div className={fieldClass}><span>API 协议</span><SelectControl name="api_protocol" ariaLabel="API 协议" value={apiProtocol} options={TRANSLATION_PROTOCOLS} onChange={nextProtocol => {setApiProtocol(nextProtocol); setDiscoveryError(''); setDiscoveredEndpoint('')}}/></div> : <div className={fieldClass}><span>Provider 类型</span><SelectControl ariaLabel="Provider 类型" value={provider} options={choices.map(item => ({value:item.name, label:`${providerLabel(kind, item.name)}${item.is_fallback ? '（备用）' : ''}`}))} onChange={setProvider}/></div>}
    {kind !== 'translation' && <div className="col-span-full grid grid-cols-[auto_minmax(0,1fr)_auto] items-center gap-3 rounded-lg bg-surface p-3"><strong className="text-[11px] text-ink">{providerLabel(kind, provider)}</strong><span className="text-[10px] text-muted">{selected?.description}</span><i className="font-mono text-[9px] not-italic text-accent">{selected?.devices.join(' / ')}</i></div>}
    {kind !== 'translation' && kind !== 'rendering' && <div className={fieldClass} key={`${kind}-${provider}-device`}><span>运行设备</span><SelectControl name="device" ariaLabel="运行设备" value={device} options={deviceOptions(selected)} onChange={setDevice}/></div>}
    {selected?.supports_batch && kind !== 'translation' && <label className={fieldClass} key={`${kind}-${provider}-batch`}>Batch Size<NumberControl ariaLabel="Batch Size" name="batch_size" min={1} step={1} defaultValue={String(defaults.batch_size ?? '')} placeholder="4"/></label>}
    {kind === 'translation' && provider !== 'passthrough' && <div className="col-span-full grid grid-cols-2 gap-3" key={`${kind}-${provider}`}>
      <label className={cn(fieldClass, 'col-span-full')}>API Base URL<input className={inputClass} name="base_url" required value={baseUrl} onChange={event => {setBaseUrl(event.target.value); setDiscoveryError(''); setDiscoveredEndpoint('')}} onBlur={() => void discoverModels()} placeholder="https://api.example.com/v1"/></label>
      <label className={cn(fieldClass, 'col-span-full')}>API Key<input className={inputClass} name="api_key" type="password" required={!(initial?.has_api_key && editingSameProvider)} value={apiKey} onChange={event => {setApiKey(event.target.value); setDiscoveryError('')}} onBlur={() => void discoverModels()} autoComplete="new-password" placeholder={initial?.has_api_key && editingSameProvider ? '密钥已保存，留空则保持不变' : '输入密钥后将自动获取模型列表'}/></label>
      <div className={cn(fieldClass, 'col-span-full')}><span>模型</span><div className="mt-2 grid grid-cols-[minmax(0,1fr)_auto] gap-2"><SelectControl name="model" ariaLabel="选择翻译模型" value={model} disabled={discovering || !modelOptions.length} placeholder={discovering ? '正在获取模型…' : '请先填写接口地址和密钥'} options={modelOptions.map(item => ({value:item, label:item}))} onChange={setModel}/><button className={buttonClass} type="button" title="重新获取模型列表" aria-label="重新获取模型列表" disabled={discovering || !baseUrl.trim()} onClick={() => void discoverModels()}>{discovering ? <LoaderCircle className="animate-spin" size={15}/> : <RefreshCw size={15}/>}<span>{discovering ? '获取中' : '获取模型'}</span></button></div>{discoveryError ? <small className="mt-2 block text-[9px] text-danger">{discoveryError}</small> : discoveredEndpoint && <small className="mt-2 block font-mono text-[9px] text-muted">已从 {discoveredEndpoint} 获取 {modelOptions.length} 个模型</small>}</div>
      <div className="col-span-full grid grid-cols-3 gap-3">
        <label className={fieldClass}>超时（秒）<NumberControl ariaLabel="接口超时" name="timeout" min={10} step={5} defaultValue={String(defaults.timeout ?? 90)}/></label>
        <label className={fieldClass}>失败重试<NumberControl ariaLabel="失败重试次数" name="retries" min={0} max={5} step={1} defaultValue={String(defaults.retries ?? 2)}/></label>
        <label className={fieldClass}>Temperature<NumberControl ariaLabel="Temperature" name="temperature" min={0} max={2} step={0.1} defaultValue={String(defaults.temperature ?? 0.2)}/></label>
      </div>
      <label className={cn(fieldClass, 'col-span-full')}>翻译 Prompt<textarea className={cn(textareaClass, 'min-h-[220px] resize-y overflow-y-auto leading-relaxed')} name="prompt" defaultValue={translationPrompt(defaults.prompt)} placeholder="漫画语气、角色称谓等翻译要求"/></label>
    </div>}
    {kind === 'rendering' && <label className={fieldClass} key={`${kind}-${provider}-font-size`}>最小字号<NumberControl ariaLabel="最小字号" name="min_font_size" min={1} step={1} defaultValue={String(defaults.min_font_size ?? 10)}/></label>}
  </div><footer className="sticky bottom-0 z-[2] flex justify-end gap-2 border-t border-line-subtle bg-[linear-gradient(180deg,rgb(29_32_28/.94),var(--color-surface)_28%)] px-6 pb-5 pt-4 [&_button]:min-w-[88px]"><button className={buttonClass} type="button" disabled={submitting} onClick={onCancel}>取消</button><button className={primaryButtonClass} disabled={submitting} type="submit">{submitting ? <><LoaderCircle className="animate-spin" size={15}/>正在保存…</> : '保存配置'}</button></footer></form>
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
