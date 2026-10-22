import {t, useLocale} from '../i18n'
import {useState} from 'react'
import {NumberControl, SelectControl} from './FormControls'
import {SettingsSection} from './SettingsControls'

export function OCRConcurrencyFields({defaults}: {defaults: Record<string, unknown>}) {
  useLocale()
  const [mode, setMode] = useState(String(defaults.concurrency_mode || 'adaptive'))
  return <SettingsSection title={t("云端识别并发")}>
    <div className="settings-grid settings-grid-three">
      <div className="min-w-0"><span className="settings-grid-label">{t("调度模式")}</span><SelectControl name="concurrency_mode" ariaLabel={t("OCR 并发模式")} value={mode} onChange={setMode} options={[{value:'adaptive', label:t("自适应并发（推荐）")}, {value:'fixed', label:t("固定并发")}, {value:'serial', label:t("串行识别")}]}/></div>
      {mode !== 'serial' && <label>{mode === 'fixed' ? t("并发数量") : t("并发上限")}<NumberControl name="max_concurrency" ariaLabel={t("OCR 并发上限")} min={1} max={16} step={1} defaultValue={String(defaults.max_concurrency ?? 8)}/></label>}
      <label>{t("请求上限（次/分钟）")}<NumberControl name="requests_per_minute" ariaLabel={t("OCR 每分钟请求上限")} min={1} max={6000} step={1} defaultValue={String(defaults.requests_per_minute ?? 60)}/></label>
    </div>
  </SettingsSection>
}
