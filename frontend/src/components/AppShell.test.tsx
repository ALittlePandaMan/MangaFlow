import {fireEvent, render, screen, waitFor} from '@testing-library/react'
import {MemoryRouter, Route, Routes} from 'react-router-dom'
import {beforeEach, describe, expect, it, vi} from 'vitest'
import {api} from '../services/api'
import {AppShell} from './AppShell'
import {GlobalDialogProvider} from './GlobalDialog'

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

function open() {
  return render(<MemoryRouter initialEntries={['/projects']}><GlobalDialogProvider><Routes>
    <Route element={<AppShell/>}><Route path="/projects" element={<p>工作区已就绪</p>}/></Route>
  </Routes></GlobalDialogProvider></MemoryRouter>)
}

describe('startup loading', () => {
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
