import {act, fireEvent, render, screen} from '@testing-library/react'
import {afterEach, expect, it, vi} from 'vitest'
import {changeLocale} from '../../../i18n'
import {EditorHeaderActions} from './EditorHeaderActions'

afterEach(() => vi.unstubAllGlobals())

it('keeps longer localized actions out of the centered navigation at desktop widths', () => {
  vi.stubGlobal('matchMedia',(query:string) => ({
    matches:1800<Number(query.match(/\d+/)?.[0]),media:query,
    addEventListener:vi.fn(),removeEventListener:vi.fn(),
  }))
  render(<EditorHeaderActions><button>Action</button></EditorHeaderActions>)
  expect(screen.getByRole('button',{name:'Action'})).toBeVisible()
  expect(screen.queryByRole('button',{name:'项目操作'})).not.toBeInTheDocument()
  act(() => changeLocale('en'))
  const trigger=screen.getByRole('button',{name:'Project actions'})
  expect(screen.queryByRole('button',{name:'Action'})).not.toBeInTheDocument()
  fireEvent.click(trigger)
  expect(screen.getByRole('button',{name:'Action'})).toBeVisible()
  expect(document.getElementById('editor-project-actions')).toHaveAttribute('data-compact','true')
  act(() => changeLocale('ja'))
  expect(screen.getByRole('button',{name:'プロジェクト操作'})).toBeInTheDocument()
})

it('collapses the 14px actions before they overlap navigation at 1600px', () => {
  vi.stubGlobal('matchMedia',(query:string) => ({
    matches:1600<Number(query.match(/\d+/)?.[0]),media:query,
    addEventListener:vi.fn(),removeEventListener:vi.fn(),
  }))
  render(<EditorHeaderActions><button>Action</button></EditorHeaderActions>)
  expect(screen.getByRole('button',{name:'项目操作'})).toBeVisible()
  expect(screen.queryByRole('button',{name:'Action'})).not.toBeInTheDocument()
})
