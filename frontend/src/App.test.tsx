import {render, screen} from '@testing-library/react'
import {MemoryRouter, Outlet} from 'react-router-dom'
import {expect, it, vi} from 'vitest'
import App from './App'

vi.mock('./components/AppShell',() => ({AppShell:() => <Outlet/>}))
vi.mock('./pages/SettingsPage',() => ({SettingsPage:() => <h1>设置已加载</h1>}))

it('uses the settings layout rather than the old page loader while the route loads',async () => {
  render(<MemoryRouter initialEntries={['/settings']}><App/></MemoryRouter>)
  expect(screen.getByRole('region',{name:'正在读取设置'})).toHaveClass('settings-workspace')
  expect(screen.getByRole('complementary',{name:'设置分类'})).toBeInTheDocument()
  expect(screen.getByRole('status',{name:'正在读取设置…'})).toHaveClass('settings-form-skeleton')
  expect(await screen.findByRole('heading',{name:'设置已加载'})).toBeInTheDocument()
})
