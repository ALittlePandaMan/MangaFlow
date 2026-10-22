import {t, useLocale} from '../i18n'
import {SettingsHeading, SettingsLayout} from './SettingsLayout'

function Placeholder({className = ''}: {className?: string}) {
  useLocale()
  return <span aria-hidden="true" className={'settings-placeholder loading-shimmer '+className}/>
}

export function SettingsFormSkeleton({label = t("正在读取设置…"), sections = [2,4,4]}: {label?: string, sections?: number[]}) {
  useLocale()
  return <div className="settings-form-skeleton" role="status" aria-label={label}>
    <span className="sr-only">{label}</span>
    <div aria-hidden="true">{sections.map((fields,index) => <div className="settings-section" key={index}>
      <div className="settings-skeleton-section-title"><Placeholder className="settings-placeholder-title"/></div>
      <div className="settings-section-body">{index === 0 ? Array.from({length:fields},(_,field) => <div className="settings-field" key={field}><Placeholder className="settings-placeholder-label"/><Placeholder className="settings-placeholder-input"/></div>) : <div className={index === 2 ? 'settings-padding-grid' : 'settings-grid'}>{Array.from({length:fields},(_,field) => <div className="settings-skeleton-control" key={field}><Placeholder className="settings-placeholder-label"/><Placeholder className="settings-placeholder-input"/></div>)}</div>}</div>
    </div>)}<div className="settings-actions"><Placeholder className="settings-placeholder-button"/></div></div>
  </div>
}

export function SettingsSkeleton() {
  useLocale()
  return <SettingsLayout loading><SettingsHeading id="detection"/><SettingsFormSkeleton/></SettingsLayout>
}
