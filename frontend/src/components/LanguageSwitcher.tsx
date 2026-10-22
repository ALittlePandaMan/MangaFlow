import {changeLocale, LOCALES, t, useLocale, type Locale} from '../i18n'
import {SelectControl} from './FormControls'

const languageOptions = LOCALES.map(({code,label}) => ({value:code,label}))

export function LanguageSwitcher() {
  const locale = useLocale()
  return <SelectControl value={locale} options={languageOptions} ariaLabel={t('界面语言')} onChange={value => changeLocale(value as Locale)}/>
}
