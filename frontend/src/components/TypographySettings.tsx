import {t, useLocale} from '../i18n'
import {useEffect, useState} from 'react'
import {RotateCcw} from 'lucide-react'
import {api} from '../services/api'
import {DEFAULT_FONT_FAMILIES} from '../constants/fonts'
import {DEFAULT_TEXT_STYLE, loadTextStyleDefaults, setTextStyleDefaults, useTextStyleStore} from '../features/settings/textStyle'
import type {FontResource, TextStyleDefaults} from '../types'
import {buttonClass, cn} from '../ui'
import {NumberControl, SelectControl} from './FormControls'
import {SettingsSection} from './SettingsControls'
import {ButtonLoading} from './LoadingUI'
import {SettingsFormSkeleton} from './SettingsSkeleton'
import {useGlobalAlert} from './GlobalAlert'

export function TypographySettings({fonts}: {fonts: FontResource[]}) {
  useLocale()
  const {alert} = useGlobalAlert()
  const [draft, setDraft] = useState<TextStyleDefaults>(() => ({...useTextStyleStore.getState().defaults}))
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [attempt, setAttempt] = useState(0)
  const [loaded, setLoaded] = useState(false)
  useEffect(() => {
    let disposed = false
    setLoading(true); setError('')
    void loadTextStyleDefaults().then(() => {
      if (!disposed) {setDraft({...useTextStyleStore.getState().defaults}); setLoaded(true)}
    }).catch(reason => {if (!disposed) setError(reason instanceof Error ? reason.message : String(reason))})
      .finally(() => {if (!disposed) setLoading(false)})
    return () => {disposed = true}
  }, [attempt])
  const change = (patch: Partial<TextStyleDefaults>) => {setDraft(current => ({...current,...patch}))}
  const save = async () => {
    if (busy || loading) return
    setBusy(true)
    try {
      const result = await api.preferences.updateTextStyle(draft)
      if (!result.text_style) throw new Error(t("服务未返回已保存的文本样式，请重试。"))
      setTextStyleDefaults(result.text_style); setDraft(result.text_style)
      void alert({title:t("保存成功"),message:t("默认样式已保存。"),tone:'success'})
    } catch (reason) {
      const message=reason instanceof Error ? reason.message : String(reason)
      void alert({title:t("保存失败"),message,tone:'danger'})
    }
    finally {setBusy(false)}
  }
  if (loading) return <SettingsFormSkeleton label={t("正在读取文本样式…")} sections={[2,6,3]}/>
  if (!loaded) return <div><p role="alert" className="settings-error">{error}</p><button type="button" className={buttonClass} onClick={() => setAttempt(current => current+1)}>{t("重新读取")}</button></div>
  const families = [...new Set([...DEFAULT_FONT_FAMILIES.map(font => font.name),...fonts.map(font => font.name),draft.font_family])]
  return <form className="settings-form" onSubmit={event => {event.preventDefault(); void save()}}>
    {error && <p className="settings-error" role="alert">{error}<button className={buttonClass} type="button" onClick={() => setAttempt(current => current+1)}>{t("重新读取")}</button></p>}
    <fieldset disabled={busy} className="m-0 min-w-0 border-0 p-0">
      <SettingsSection title={t("初始文字样式")}>
        <div className="settings-grid">
          <div><span className="settings-grid-label">{t("字体")}</span><SelectControl ariaLabel={t("默认字体")} value={draft.font_family} options={families.map(value => ({value,label:value}))} onChange={font_family => change({font_family})}/></div>
          <div><span className="settings-grid-label">{t("字重")}</span><SelectControl ariaLabel={t("默认字重")} value={String(draft.font_weight)} options={[100,200,300,400,500,600,700,800,900].map(value => ({value:String(value),label:value+(value===400 ? t(" · 常规") : value===700 ? t(" · 粗体") : '')}))} onChange={value => change({font_weight:Number(value)})}/></div>
        </div>
        <div className="settings-grid settings-grid-three">
          <div><span className="settings-grid-label">{t("自动字号")}</span><label className="settings-style-toggle"><input type="checkbox" checked={draft.auto_font_size} onChange={event => change({auto_font_size:event.target.checked})}/>{t("按文本框拟合")}</label></div>
          <label>{t("固定字号")}<NumberControl ariaLabel={t("默认固定字号")} value={draft.font_size} min={1} max={512} disabled={draft.auto_font_size} onChange={font_size => change({font_size})}/></label>
          <div><span className="settings-grid-label">{t("文字方向")}</span><SelectControl ariaLabel={t("默认文字方向")} value={draft.orientation} options={[{value:'auto',label:t("跟随文本框")},{value:'horizontal',label:t("横排")},{value:'vertical',label:t("竖排")}]} onChange={value => change({orientation:value as TextStyleDefaults['orientation']})}/></div>
        </div>
        <div className="settings-grid settings-grid-three">
          <label>{t("文字颜色")}<input type="color" className="settings-color-input" value={draft.text_color} onChange={event => change({text_color:event.target.value})}/></label>
          <label>{t("描边颜色")}<input type="color" className="settings-color-input" value={draft.stroke_color} onChange={event => change({stroke_color:event.target.value})}/></label>
          <div><span className="settings-grid-label">{t("对齐")}</span><SelectControl ariaLabel={t("默认对齐")} value={draft.alignment} options={[{value:'left',label:t("左对齐")},{value:'center',label:t("居中")},{value:'right',label:t("右对齐")}]} onChange={value => change({alignment:value as TextStyleDefaults['alignment']})}/></div>
        </div>
        <div className="settings-grid settings-grid-three">
          <label>{t("行距")}<NumberControl ariaLabel={t("默认行距")} value={draft.line_spacing} min={0.5} max={3} step={0.05} onChange={line_spacing => change({line_spacing})}/></label>
          <label>{t("字距")}<NumberControl ariaLabel={t("默认字距")} value={draft.character_spacing} min={-5} max={20} step={0.5} onChange={character_spacing => change({character_spacing})}/></label>
          <label>{t("描边宽度")}<NumberControl ariaLabel={t("默认描边宽度")} value={draft.stroke_width} min={0} max={20} step={0.5} onChange={stroke_width => change({stroke_width})}/></label>
        </div>
      </SettingsSection>
      <div className="settings-style-preview" aria-label={t("样式预览")}><p style={{fontFamily:'"'+draft.font_family+'",sans-serif',fontWeight:draft.font_weight,fontSize:Math.min(48,draft.font_size),color:draft.text_color,WebkitTextStroke:draft.stroke_width+'px '+draft.stroke_color,lineHeight:draft.line_spacing,letterSpacing:draft.character_spacing,textAlign:draft.alignment,writingMode:draft.orientation === 'vertical' ? 'vertical-rl' : 'horizontal-tb'}}>你好，漫画世界。</p></div>
    </fieldset>
    <footer className="settings-actions"><button type="button" className={buttonClass} disabled={busy} onClick={() => change({...DEFAULT_TEXT_STYLE})}><RotateCcw size={16}/>{t("恢复默认")}</button><button type="submit" className={cn(buttonClass,'settings-primary')} disabled={busy}>{busy ? <ButtonLoading label={t("正在保存…")}/> : t("保存默认样式")}</button></footer>
  </form>
}
