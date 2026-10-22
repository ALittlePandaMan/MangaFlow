import {useEffect, useRef, useState} from 'react'
import {Check, Download, ExternalLink, Search, Trash2, Type, Upload, X} from 'lucide-react'
import {t, useLocale} from '../i18n'
import {DEFAULT_FONT_FAMILIES} from '../constants/fonts'
import {api} from '../services/api'
import type {FontPreset, FontResource} from '../types'
import {buttonClass, cn, ghostIconButtonClass, iconButtonClass, inputClass} from '../ui'
import {ButtonLoading} from './LoadingUI'
import {SelectControl} from './FormControls'
import {SettingsSection} from './SettingsControls'
import {useGlobalAlert} from './GlobalAlert'

const artwork = import.meta.glob('../assets/font-previews/*.svg', {eager:true, query:'?url', import:'default'}) as Record<string,string>
const message = (reason: unknown) => t(reason instanceof Error ? reason.message : String(reason))
const languageLabel = (code: string) => ({'zh-Hans':t('简体中文'),'zh-Hant':t('繁体中文'),ja:t('日语'),en:t('英语')}[code] || code)
const categoryLabel = (category: string) => ({handwriting:t('手写体'),display:t('标题与拟声词'),serif:t('宋体风格'),'sans-serif':t('圆体')}[category] || category)
const defaultSample = (preset?: FontPreset) => preset?.languages[0] === 'en' ? 'Hello, manga world!' : preset?.languages[0] === 'ja' ? 'こんにちは、漫画の世界。' : preset?.languages[0] === 'zh-Hant' ? '你好，漫畫世界。' : '你好，漫画世界。'
const normalize = (text: string) => text.normalize('NFKC').toLowerCase()

export function FontManager({fonts, onChanged}: {fonts: FontResource[], onChanged: (message: string) => Promise<void>}) {
  useLocale()
  const {alert} = useGlobalAlert()
  const [presets, setPresets] = useState<FontPreset[]>([])
  const [loading, setLoading] = useState(true)
  const [catalogError, setCatalogError] = useState('')
  const [attempt, setAttempt] = useState(0)
  const [query, setQuery] = useState('')
  const [language, setLanguage] = useState('all')
  const [view, setView] = useState<'presets' | 'installed'>('presets')
  const [preview, setPreview] = useState('')
  const [busy, setBusy] = useState('')
  const pending = useRef(false)
  const input = useRef<HTMLInputElement>(null)
  useEffect(() => {
    let disposed = false
    setLoading(true); setCatalogError('')
    void api.fonts.presets().then(result => {if (!disposed) setPresets(result)})
      .catch(reason => {if (!disposed) setCatalogError(message(reason))})
      .finally(() => {if (!disposed) setLoading(false)})
    return () => {disposed = true}
  }, [attempt])
  useEffect(() => {
    if (typeof FontFace === 'undefined' || !document.fonts) return
    let disposed = false
    const registered: FontFace[] = []
    for (const font of fonts) {
      const source = font.url || '/media/'+font.path.split('/').map(encodeURIComponent).join('/')
      const face = new FontFace(font.name,'url('+JSON.stringify(source)+')')
      void face.load().then(loaded => {if (!disposed) {document.fonts.add(loaded); registered.push(loaded)}}).catch(() => undefined)
    }
    return () => {disposed = true; registered.forEach(face => document.fonts.delete(face))}
  }, [fonts])
  const mutate = async (key: string, action: () => Promise<string>) => {
    if (pending.current) return
    pending.current = true; setBusy(key)
    try {
      const text = await action()
      await onChanged(text)
      void alert({title:t('字体管理'),message:text,tone:'success'})
    }
    catch (reason) {void alert({title:t('字体管理'),message:message(reason),tone:'danger'})}
    finally {pending.current = false; setBusy(''); if (input.current) input.current.value = ''}
  }
  const upload = (file: File) => mutate('upload', async () => {
    const font = await api.fonts.upload(file)
    return t('字体“{{v0}}”已添加。', {v0:font.name})
  })
  const install = (preset: FontPreset) => mutate(preset.id, async () => {
    await api.fonts.install(preset.id)
    return t('字体“{{v0}}”已下载，可在排版中使用。', {v0:preset.name})
  })
  const remove = (font: FontResource) => mutate(font.filename, async () => {
    await api.fonts.remove(font.filename)
    return t('字体“{{v0}}”已删除。', {v0:font.name})
  })
  const words = normalize(query.trim()).split(/\s+/).filter(Boolean)
  const matches = (...values: string[]) => words.every(word => normalize(values.join(' ')).includes(word))
  const visiblePresets = presets.filter(preset => (language === 'all' || preset.languages.includes(language)) && matches(
    preset.name, preset.family, preset.description, t(preset.description), categoryLabel(preset.category), preset.category,
    ...preset.languages, ...preset.languages.map(languageLabel)))
  const builtin = DEFAULT_FONT_FAMILIES.filter(font => matches(font.name, font.label, t('内置'), ...['zh-Hans','zh-Hant','ja','en'].map(languageLabel)))
  const custom = fonts.filter(font => {
    const preset = presets.find(item => item.id === font.preset_id)
    return matches(font.name, font.filename, preset?.name || '', preset?.description || '', t(preset?.description || ''),
      ...(preset?.languages.map(languageLabel) || []), preset ? categoryLabel(preset.category) : '', font.preset_id ? t('已下载') : t('自定义'))
  })
  const downloaded = fonts.filter(font => font.preset_id).length
  const count = view === 'presets' ? visiblePresets.length : builtin.length + custom.length
  return <div className="settings-font-manager">
    <div className="settings-font-toolbar">
      <strong>{t('{{v0}} 种内置 · {{v1}} 种已下载 · {{v2}} 种自定义', {v0:DEFAULT_FONT_FAMILIES.length,v1:downloaded,v2:fonts.length-downloaded})}</strong>
      <button className={buttonClass} type="button" disabled={Boolean(busy)} onClick={() => input.current?.click()}>{busy === 'upload' ? <ButtonLoading label={t('正在上传…')}/> : <><Upload size={16} aria-hidden="true"/>{t('上传字体')}</>}</button>
      <input ref={input} aria-label={t('上传字体文件')} hidden type="file" accept=".ttf,.otf,font/ttf,font/otf" onChange={event => {const file = event.target.files?.[0]; if (file) void upload(file)}}/>
    </div>
    <div className="font-library-navigation" role="group" aria-label={t('字体列表视图')}>
      <button type="button" aria-pressed={view === 'presets'} onClick={() => setView('presets')}>{t('字体预设')}<span>{presets.length}</span></button>
      <button type="button" aria-pressed={view === 'installed'} onClick={() => setView('installed')}>{t('已安装字体')}<span>{DEFAULT_FONT_FAMILIES.length+fonts.length}</span></button>
    </div>
    <div className="font-library-filters">
      <div className="font-library-search"><Search size={18} aria-hidden="true"/><input type="search" className={inputClass} aria-label={t('搜索字体')} placeholder={t('搜索名称、语言或风格…')} value={query} maxLength={100} onChange={event => setQuery(event.target.value)}/>{query && <button type="button" className={ghostIconButtonClass} aria-label={t('清空搜索')} onClick={() => setQuery('')}><X size={16} aria-hidden="true"/></button>}</div>
      {view === 'presets' && <SelectControl ariaLabel={t('字体语言')} value={language} onChange={setLanguage} options={[{value:'all',label:t('全部语言')},...['zh-Hans','zh-Hant','ja','en'].map(value => ({value,label:languageLabel(value)}))]}/>}
    </div>
    {busy && <p className="font-library-feedback" role="status">{busy === 'upload' ? t('正在上传…') : presets.some(preset => preset.id === busy) ? t('正在下载并校验字体，请稍候…') : t('正在删除')}</p>}
    {view === 'presets' ? <section className="font-preset-section" aria-label={t('字体预设')}>
      {loading ? <p role="status"><ButtonLoading label={t('正在读取字体预设…')}/></p> : catalogError ? <div className="font-library-retry"><p className="settings-error" role="alert">{catalogError}</p><button type="button" className={buttonClass} onClick={() => setAttempt(value => value+1)}>{t('重新加载')}</button></div> : <>
        <p className="font-library-count" role="status">{t('找到 {{v0}} 种字体', {v0:count})}</p>
        <div className="font-preset-grid">{visiblePresets.map(preset => {
          const saved = fonts.find(font => font.preset_id === preset.id || font.filename === preset.installed_filename)
          const sample = artwork[`../assets/font-previews/${preset.id}.svg`]
          return <article className="font-preset-card" key={preset.id} aria-label={preset.name}>
            <div className="font-preset-heading"><div><h2>{preset.name}</h2>{preset.family !== preset.name && <span>{preset.family}</span>}</div><span className="font-preset-kind">{categoryLabel(preset.category)}</span></div>
            {saved ? <p className="font-preset-live" style={{fontFamily:JSON.stringify(saved.name)+',sans-serif'}}>{preview || defaultSample(preset)}</p> : sample ? <span role="img" aria-label={t('{{v0}}字体效果', {v0:preset.name})} className="font-preset-artwork" style={{maskImage:'url('+JSON.stringify(sample)+')'}}/> : null}
            <p className="font-preset-description">{t(preset.description)}</p>
            <div className="font-preset-languages">{preset.languages.map(code => <span key={code}>{languageLabel(code)}</span>)}</div>
            <footer><div><span className="font-preset-size">{preset.size_bytes < 1024*1024 ? Math.ceil(preset.size_bytes/1024)+' KB' : (preset.size_bytes/(1024*1024)).toFixed(1)+' MB'}</span><a href={preset.source_url} target="_blank" rel="noopener noreferrer">{t('来源')}<ExternalLink aria-hidden="true" size={12}/></a><a href={preset.license_url} target="_blank" rel="noopener noreferrer">{preset.license}</a></div><button type="button" className={cn(buttonClass,!saved && 'font-preset-download')} aria-label={t('下载{{v0}}', {v0:preset.name})} disabled={Boolean(busy) || Boolean(saved)} onClick={() => void install(preset)}>{busy === preset.id ? <ButtonLoading label={t('下载中…')}/> : saved ? <><Check aria-hidden="true" size={16}/>{t('已下载')}</> : <><Download aria-hidden="true" size={16}/>{t('下载')}</>}</button></footer>
          </article>
        })}</div>
        {!count && <div className="font-library-empty"><Search aria-hidden="true" size={26}/><strong>{t('没有找到匹配的字体')}</strong><button type="button" className={buttonClass} onClick={() => {setQuery(''); setLanguage('all')}}>{t('清除筛选')}</button></div>}
      </>}
    </section> : <>
      <label className="settings-font-preview-input">{t('预览文字')}<input className={inputClass} value={preview} placeholder={defaultSample()} onChange={event => setPreview(event.target.value)} maxLength={200}/></label>
      <SettingsSection title={t('可用字体')}><div className="settings-font-list">
        {builtin.map(font => <article className="settings-font-row" key={font.name}><div className="settings-font-info"><Type size={20} aria-hidden="true"/><div><strong>{font.name}</strong><small>{font.label}</small></div><span className="settings-badge">{t('内置')}</span></div><p style={{fontFamily:JSON.stringify(font.name)+','+(font.name.includes('Serif') ? 'serif' : 'sans-serif'),fontWeight:font.name.includes('Bold') ? 700 : 400}}>{preview || defaultSample()}</p></article>)}
        {custom.map(font => <article className="settings-font-row" key={font.filename}><div className="settings-font-info"><Type size={20} aria-hidden="true"/><div><strong>{font.name}</strong><small>{font.preset_id ? t('已下载') : font.filename}</small></div><button className={cn(iconButtonClass,'icon-button-danger')} type="button" disabled={Boolean(busy)} aria-label={t('删除')+font.name} onClick={() => void remove(font)}>{busy === font.filename ? <ButtonLoading compact label={t('正在删除')}/> : <Trash2 aria-hidden="true" size={17}/>}</button></div><p style={{fontFamily:JSON.stringify(font.name)+',sans-serif'}}>{preview || defaultSample(presets.find(preset => preset.id === font.preset_id))}</p></article>)}
        {!count && <p className="font-library-empty">{t('没有找到匹配的字体')}</p>}
      </div></SettingsSection>
    </>}
  </div>
}
