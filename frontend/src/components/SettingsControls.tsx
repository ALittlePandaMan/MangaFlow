import type {ReactNode} from 'react'

export function SettingsSection({title, children}: {title: string, children: ReactNode}) {
  return <section className="settings-section"><h2>{title}</h2><div className="settings-section-body">{children}</div></section>
}

export function SettingsField({label, children}: {label: string, children: ReactNode}) {
  return <div className="settings-field"><span className="settings-field-label">{label}</span><div className="settings-field-control">{children}</div></div>
}
