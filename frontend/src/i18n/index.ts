import i18next from 'i18next'
import {initReactI18next, useTranslation} from 'react-i18next'
import en from './locales/en.json'
import ja from './locales/ja.json'
import traditional from './locales/zh-TW.json'

export const LOCALE_STORAGE_KEY = 'mangaflow.locale'
export const LOCALES = [
  {code:'zh-CN', label:'简体中文'},
  {code:'zh-TW', label:'繁體中文'},
  {code:'en', label:'English'},
  {code:'ja', label:'日本語'},
] as const
export type Locale = typeof LOCALES[number]['code']

export function normalizeLocale(value: string | null | undefined): Locale | null {
  const tag = value?.toLowerCase().replaceAll('_','-') || ''
  if (/^zh(?:-|$)/.test(tag)) return /(?:hant|tw|hk|mo)/.test(tag) ? 'zh-TW' : 'zh-CN'
  if (/^en(?:-|$)/.test(tag)) return 'en'
  if (/^ja(?:-|$)/.test(tag)) return 'ja'
  return null
}

export function readLocale(): Locale {
  try {
    const stored = normalizeLocale(window.localStorage.getItem(LOCALE_STORAGE_KEY))
    if (stored) return stored
  } catch { /* Language switching still works when storage is blocked. */ }
  if (typeof navigator !== 'undefined') {
    for (const language of navigator.languages || [navigator.language]) {
      const supported = normalizeLocale(language)
      if (supported) return supported
    }
  }
  return 'zh-CN'
}

export const i18n = i18next.createInstance()
void i18n.use(initReactI18next).init({
  lng: readLocale(),
  supportedLngs: LOCALES.map(item => item.code),
  fallbackLng: 'zh-CN',
  load: 'currentOnly',
  resources: {'zh-CN':{translation:{}},'zh-TW':{translation:traditional},en:{translation:en},ja:{translation:ja}},
  keySeparator:false,
  nsSeparator:false,
  initAsync:false,
  interpolation:{escapeValue:false},
  react:{useSuspense:false},
})

function applyLocale(language: string) {
  const locale = normalizeLocale(language) || 'zh-CN'
  if (typeof document !== 'undefined') {
    document.documentElement.lang = locale
    document.documentElement.dir = 'ltr'
  }
}
applyLocale(i18n.language)
i18n.on('languageChanged', applyLocale)

export function changeLocale(locale: Locale): void {
  if (!LOCALES.some(item => item.code === locale)) return
  void i18n.changeLanguage(locale)
  try {window.localStorage.setItem(LOCALE_STORAGE_KEY,locale)} catch { /* Session-only preference. */ }
}

if (typeof window !== 'undefined') window.addEventListener('storage', event => {
  if (event.key !== LOCALE_STORAGE_KEY) return
  void i18n.changeLanguage(normalizeLocale(event.newValue) || readLocale())
})

/** Source-language keys keep the Chinese interface readable during migration. */
export function t(source: string, values?: Record<string, unknown>): string {
  return i18n.t(source,{...values,defaultValue:source}) as string
}

/** Subscribe each component, including memoized panels, without remounting drafts. */
export function useLocale(): Locale {
  const {i18n: instance} = useTranslation()
  return normalizeLocale(instance.language) || 'zh-CN'
}
