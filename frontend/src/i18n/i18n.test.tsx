import {act, fireEvent, render, screen} from '@testing-library/react'
import {useState} from 'react'
import {afterEach, describe, expect, it, vi} from 'vitest'
import {LanguageSwitcher} from '../components/LanguageSwitcher'
import {SETTINGS_PAGES} from '../features/settings/navigation'
import {api} from '../services/api'
import {changeLocale, i18n, LOCALES, LOCALE_STORAGE_KEY, normalizeLocale, readLocale, t, useLocale} from './index'
import en from './locales/en.json'
import ja from './locales/ja.json'
import traditional from './locales/zh-TW.json'

afterEach(() => {vi.restoreAllMocks(); localStorage.removeItem(LOCALE_STORAGE_KEY)})

describe('interface language', () => {
  it.each([
    ['zh-Hans-CN','zh-CN'],['zh_CN','zh-CN'],['zh-Hant-HK','zh-TW'],['zh-TW','zh-TW'],
    ['en-GB','en'],['ja-JP','ja'],['fr',null],[null,null],
  ])('normalizes %s to %s', (input, expected) => {expect(normalizeLocale(input)).toBe(expected)})

  it('prefers the saved language, then a supported browser language', () => {
    vi.spyOn(navigator,'languages','get').mockReturnValue(['fr-FR','ja-JP'])
    localStorage.removeItem(LOCALE_STORAGE_KEY)
    expect(readLocale()).toBe('ja')
    localStorage.setItem(LOCALE_STORAGE_KEY,'zh-Hant')
    expect(readLocale()).toBe('zh-TW')
    localStorage.setItem(LOCALE_STORAGE_KEY,'invalid')
    expect(readLocale()).toBe('ja')
  })

  it('updates document language and persists all four choices', () => {
    for (const locale of LOCALES) {
      act(() => changeLocale(locale.code))
      expect(i18n.language).toBe(locale.code)
      expect(document.documentElement.lang).toBe(locale.code)
      expect(document.documentElement.dir).toBe('ltr')
      expect(localStorage.getItem(LOCALE_STORAGE_KEY)).toBe(locale.code)
    }
  })

  it('still switches when browser storage is blocked', () => {
    vi.spyOn(Storage.prototype,'setItem').mockImplementation(() => {throw new Error('blocked')})
    vi.spyOn(Storage.prototype,'getItem').mockImplementation(() => {throw new Error('blocked')})
    expect(() => changeLocale('ja')).not.toThrow()
    expect(t('文字检测')).toBe('文字検出')
    expect(() => readLocale()).not.toThrow()
  })

  it('syncs a language preference from another tab without reloading', () => {
    window.dispatchEvent(new StorageEvent('storage',{key:LOCALE_STORAGE_KEY,newValue:'en'}))
    expect(t('文字检测')).toBe('Text detection')
  })

  it('preserves literal user content supplied as interpolation values', () => {
    changeLocale('en')
    const title='<draft>{{name}} 日本語 / 简体中文'
    expect(t('{{v0}} 封面',{v0:title})).toBe(`${title} cover`)
    expect(t('A user-defined model identifier')).toBe('A user-defined model identifier')
  })

  it('uses English page-count plurals without changing Chinese and Japanese counts', () => {
    changeLocale('en')
    expect(t('{{count}} 页',{count:1})).toBe('1 page')
    expect(t('{{count}} 页',{count:2})).toBe('2 pages')
    changeLocale('zh-TW')
    expect(t('{{count}} 页',{count:2})).toBe('2 頁')
    changeLocale('ja')
    expect(t('{{count}} 页',{count:2})).toBe('2 ページ')
  })

  it('refreshes static navigation labels without replacing API objects or methods', () => {
    const projects=api.projects, exporter=api.projects.export, page=SETTINGS_PAGES[0]
    changeLocale('en')
    expect(page.label).toBe('Text detection')
    changeLocale('ja')
    expect(page.label).toBe('文字検出')
    expect(api.projects).toBe(projects)
    expect(api.projects.export).toBe(exporter)
  })

  it('has matching catalog keys and interpolation placeholders', () => {
    const keys=Object.keys(en).sort()
    const placeholders=(text:string) => [...text.matchAll(/{{\s*([^}]+)\s*}}/g)].map(match => match[1].trim()).sort()
    for (const catalog of [en,ja,traditional]) {
      expect(Object.keys(catalog).sort()).toEqual(keys)
      for (const [key,value] of Object.entries(catalog)) {
        expect(value.trim(),key).not.toBe('')
        expect(placeholders(value),key).toEqual(placeholders(key))
      }
    }
  })
})

function DraftPanel() {
  useLocale()
  const [value,setValue]=useState('')
  return <><LanguageSwitcher/><h1>{t('文字检测')}</h1><input aria-label="draft" value={value} onChange={event => setValue(event.target.value)}/></>
}

describe('settings language selector', () => {
  it('switches all four languages in place and preserves an unsaved draft', () => {
    render(<DraftPanel/>)
    fireEvent.change(screen.getByRole('textbox'),{target:{value:'未保存の下書き'}})
    for (const [label,heading] of [['English','Text detection'],['日本語','文字検出'],['繁體中文','文字檢測'],['简体中文','文字检测']]) {
      fireEvent.click(screen.getByRole('button'))
      fireEvent.click(screen.getByRole('option',{name:label}))
      expect(screen.getByRole('heading',{name:heading})).toBeInTheDocument()
      expect(screen.getByRole('textbox')).toHaveValue('未保存の下書き')
      expect(screen.queryByRole('listbox')).not.toBeInTheDocument()
    }
  })

  it('supports keyboard navigation, Escape and outside click', () => {
    render(<LanguageSwitcher/>)
    const trigger=screen.getByRole('button')
    fireEvent.keyDown(trigger,{key:'ArrowDown'})
    expect(screen.getByRole('option',{name:'简体中文'})).toHaveAttribute('aria-selected','true')
    fireEvent.keyDown(trigger,{key:'End'})
    fireEvent.keyDown(trigger,{key:'Enter'})
    expect(document.documentElement.lang).toBe('ja')
    expect(trigger).toHaveTextContent('日本語')
    fireEvent.click(trigger)
    fireEvent.keyDown(trigger,{key:'Escape'})
    expect(trigger).toHaveFocus()
    expect(trigger).toHaveAttribute('aria-expanded','false')
    fireEvent.click(trigger)
    fireEvent.pointerDown(document.body)
    expect(screen.queryByRole('listbox')).not.toBeInTheDocument()
  })
})
