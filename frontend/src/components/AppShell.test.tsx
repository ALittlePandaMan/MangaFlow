import {fireEvent, render, screen, waitFor} from '@testing-library/react'
import {MemoryRouter, Route, Routes} from 'react-router-dom'
import {afterEach, beforeEach, describe, expect, it, vi} from 'vitest'
import {api} from '../services/api'
import {AppShell} from './AppShell'
import {GlobalDialogProvider} from './GlobalDialog'
import {SettingsLayout} from './SettingsLayout'

vi.mock('./ThemeToggle', () => ({ThemeToggle: () => null}))
vi.mock('../pages/SettingsPage', () => ({
  RecommendedConfigurationWizard: ({available, fonts}: {available: unknown[], fonts: unknown[]}) =>
    <p>配置向导：{available.length} 个模型，{fonts.length} 个字体</p>,
}))

const ready = {ready: true, first_run: false, validated: true, stages: []}
beforeEach(() => {
  vi.restoreAllMocks()
  vi.spyOn(api.models, 'setupStatus').mockResolvedValue(ready)
  vi.spyOn(api.preferences, 'get').mockResolvedValue({shortcuts: {}})
  vi.spyOn(api.models, 'list').mockResolvedValue({available: [], configured: []})
  vi.spyOn(api.fonts, 'list').mockResolvedValue([])
})
afterEach(() => vi.unstubAllGlobals())

function open(path = '/projects') {
  return render(<MemoryRouter initialEntries={[path]}><GlobalDialogProvider><Routes>
    <Route element={<AppShell/>}>
      <Route path="/projects" element={<p>工作区已就绪</p>}/>
      <Route path="/settings" element={<SettingsLayout onNavigate={vi.fn()}><p>设置表单</p></SettingsLayout>}/>
    </Route>
  </Routes></GlobalDialogProvider></MemoryRouter>)
}

describe('startup loading', () => {
  it('navigates into settings normally, then uses the same topbar icon to toggle the compact drawer',async () => {
    vi.stubGlobal('matchMedia',vi.fn(() => ({matches:true,addEventListener:vi.fn(),removeEventListener:vi.fn()})))
    open()
    await screen.findByText('工作区已就绪')
    fireEvent.click(screen.getByRole('link',{name:'设置'}))
    expect(await screen.findByText('设置表单')).toBeInTheDocument()
    const trigger = screen.getByRole('button',{name:'设置'})
    expect(trigger).toHaveAttribute('aria-expanded','false')
    expect(document.querySelector('.settings-mobile-bar')).not.toBeInTheDocument()
    fireEvent.click(trigger)
    expect(trigger).toHaveAttribute('aria-expanded','true')
    expect(screen.getByRole('complementary',{name:'设置分类'})).toHaveClass('is-open')
    fireEvent.click(trigger)
    expect(trigger).toHaveAttribute('aria-expanded','false')
    fireEvent.click(trigger)
    fireEvent.click(screen.getByRole('link',{name:'项目'}))
    expect(await screen.findByText('工作区已就绪')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('link',{name:'设置'}))
    expect(await screen.findByText('设置表单')).toBeInTheDocument()
    expect(screen.getByRole('button',{name:'设置'})).toHaveAttribute('aria-expanded','false')
  })

  it('keeps the desktop topbar settings entry as navigation without a drawer toggle',async () => {
    open('/settings')
    await screen.findByText('设置表单')
    expect(screen.getByRole('link',{name:'设置'})).toHaveAttribute('href','/settings')
    expect(screen.queryByRole('button',{name:'设置'})).not.toBeInTheDocument()
    expect(document.querySelector('.settings-backdrop')).not.toBeInTheDocument()
  })
  it('opens a configured workspace without fetching the model catalogue or font list', async () => {
    open()
    expect(await screen.findByText('工作区已就绪')).toBeInTheDocument()
    expect(api.models.list).not.toHaveBeenCalled()
    expect(api.fonts.list).not.toHaveBeenCalled()
    expect(api.preferences.get).toHaveBeenCalledOnce()
  })

  it('still loads the required first-run wizard and keeps the workspace gated', async () => {
    vi.mocked(api.models.setupStatus).mockResolvedValue({...ready, ready: false, first_run: true})
    open()
    expect(await screen.findByText('配置向导：0 个模型，0 个字体')).toBeInTheDocument()
    expect(api.models.list).toHaveBeenCalledOnce()
    expect(api.fonts.list).toHaveBeenCalledOnce()
    expect(screen.queryByText('工作区已就绪')).not.toBeInTheDocument()
  })

  it('allows retry when startup validation fails', async () => {
    vi.mocked(api.models.setupStatus).mockRejectedValueOnce(new Error('暂时无法连接'))
    open()
    fireEvent.click(await screen.findByRole('button', {name: '重新检测'}))
    await waitFor(() => expect(screen.getByText('工作区已就绪')).toBeInTheDocument())
    expect(api.models.setupStatus).toHaveBeenCalledTimes(2)
  })
})
