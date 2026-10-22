import {act, fireEvent, render, screen, waitFor, within} from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import {MemoryRouter} from 'react-router-dom'
import {afterEach, beforeEach, describe, expect, it, vi} from 'vitest'
import {GlobalDialogProvider} from '../components/GlobalDialog'
import {SettingsNavigationControl, SettingsNavigationProvider} from '../components/SettingsNavigation'
import {SETTINGS_PAGES} from '../features/settings/navigation'
import {api} from '../services/api'
import type {ModelConfiguration, ModelDescriptor} from '../types'
import {SettingsPage} from './SettingsPage'
import {changeLocale} from '../i18n'

vi.mock('../components/LoadingUI', async original => ({...await original<typeof import('../components/LoadingUI')>(),useMinimumLoadingTime:(value:boolean) => value}))
const descriptor = {kind:'detection',name:'rfdetr',devices:['cpu','cuda'],description:'RF-DETR',supports_batch:false,installed:true,orientations:[],capabilities:{},is_fallback:false} as ModelDescriptor
const config: ModelConfiguration = {id:'detect',kind:'detection',provider:'rfdetr',name:'RF-DETR',enabled:true,is_default:true,has_api_key:false,config:{device:'auto'},capabilities:{...descriptor}}
let compact = true
let resizeListeners: Set<() => void>

function openSettings() {
  return render(<MemoryRouter initialEntries={['/settings']}><SettingsNavigationProvider>
    <header><nav className="app-navigation"><SettingsNavigationControl/></nav></header>
    <GlobalDialogProvider><SettingsPage/></GlobalDialogProvider>
  </SettingsNavigationProvider></MemoryRouter>)
}

beforeEach(() => {
  vi.restoreAllMocks()
  compact = true
  resizeListeners = new Set()
  vi.stubGlobal('matchMedia',vi.fn(() => ({get matches() {return compact},addEventListener:(_type:string,listener:() => void) => resizeListeners.add(listener),removeEventListener:(_type:string,listener:() => void) => resizeListeners.delete(listener)})))
  vi.spyOn(api.models,'list').mockResolvedValue({available:[descriptor],configured:[config]})
  vi.spyOn(api.models,'recommendedStatus').mockResolvedValue({state:'idle',installed:false,configured:false,models:[],error:null})
  vi.spyOn(api.fonts,'list').mockResolvedValue([])
})
afterEach(() => {
  Reflect.deleteProperty(HTMLElement.prototype, 'animate')
  vi.unstubAllGlobals()
})

describe('settings workspace', () => {
  it('uses a short English sidebar label with a descriptive tooltip and preserves the dialog title',async () => {
    changeLocale('en')
    const user=userEvent.setup()
    openSettings()
    await waitFor(()=>expect(screen.getByRole('button',{name:'Quick setup'})).toBeEnabled())
    const sidebar=screen.getByRole('complementary',{name:'Settings categories'})
    const quickSetup=within(sidebar).getByRole('button',{name:'Quick setup'})
    expect(quickSetup).toHaveAttribute('title','Install recommended models and settings')
    expect(within(sidebar).queryByRole('button',{name:'Install recommended setup'})).not.toBeInTheDocument()
    await user.click(quickSetup)
    expect(screen.getByRole('dialog',{name:'Install recommended setup'})).toBeVisible()
    await user.click(screen.getByRole('button',{name:'Cancel'}))
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  })
  it('keeps the sidebar and form layout in place while reading settings',async () => {
    let finish!: (value: {available: ModelDescriptor[], configured: ModelConfiguration[]}) => void
    vi.mocked(api.models.list).mockReturnValueOnce(new Promise(resolve => {finish=resolve}))
    openSettings()
    expect(screen.getByRole('region',{name:'正在读取设置'})).toHaveClass('settings-workspace')
    expect(screen.getByRole('heading',{level:1,name:'文字检测'})).toBeVisible()
    expect(screen.getByRole('status',{name:'正在读取设置…'})).toHaveClass('settings-form-skeleton')
    const sidebar = screen.getByRole('complementary',{name:'设置分类'})
    expect(within(sidebar).getAllByRole('button')).toHaveLength(SETTINGS_PAGES.length+2)
    expect(within(sidebar).getByRole('button',{name:'编辑文字检测配置'})).toBeDisabled()
    expect(document.querySelector('.loading-skeleton-page')).not.toBeInTheDocument()
    await act(async () => {finish({available:[descriptor],configured:[config]})})
    expect(screen.queryByRole('status',{name:'正在读取设置…'})).not.toBeInTheDocument()
    expect(screen.getByRole('complementary',{name:'设置分类'})).toHaveClass('settings-sidebar')
    expect(screen.getByLabelText('文字检测置信度')).toBeVisible()
  })
  it('provides a retry instead of exposing empty forms after an initial request fails',async () => {
    vi.mocked(api.models.list).mockRejectedValueOnce(new Error('服务暂不可用'))
    const user = userEvent.setup()
    openSettings()
    expect(await screen.findByRole('alert')).toHaveTextContent('服务暂不可用')
    expect(screen.queryByRole('button',{name:'保存配置'})).not.toBeInTheDocument()
    await user.click(screen.getByRole('button',{name:'重新加载'}))
    expect(await screen.findByLabelText('文字检测置信度')).toBeVisible()
    expect(api.models.list).toHaveBeenCalledTimes(2)
  })
  it('closes the mobile drawer and shows a simple fixed-model confirmation',async () => {
    const user = userEvent.setup()
    openSettings()
    await screen.findByLabelText('文字检测置信度')
    await user.click(screen.getByRole('button',{name:'设置'}))
    await user.click(screen.getByRole('button',{name:'安装推荐配置'}))
    const dialog = await screen.findByRole('dialog',{name:'安装推荐配置'})
    expect(screen.getByRole('button',{name:'设置'})).toHaveAttribute('aria-expanded','false')
    expect(within(dialog).queryByRole('navigation')).not.toBeInTheDocument()
    expect(dialog).toHaveTextContent('RF-DETR')
    expect(dialog).toHaveTextContent('PaddleOCR-VL 1.6')
    expect(dialog).toHaveTextContent('LaMa 复杂背景修复')
    expect(dialog).toHaveTextContent('Pillow 排版渲染')
    await user.click(within(dialog).getByRole('button',{name:'取消'}))
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  })
  it('uses category-only navigation with inline forms and a single install action',async () => {
    openSettings()
    await screen.findByLabelText('文字检测置信度')
    expect(screen.getByRole('heading',{level:1,name:'文字检测'})).toBeVisible()
    const nav = screen.getByRole('complementary',{name:'设置分类'})
    expect(within(nav).queryByText('RF-DETR')).not.toBeInTheDocument()
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    expect(within(nav).getByRole('button',{name:'安装推荐配置'})).toBeVisible()
    expect(screen.queryByText('保存后用于后续任务')).not.toBeInTheDocument()
  })
  it('returns each section to the top while preserving drafts without refetching models',async () => {
    const user = userEvent.setup()
    openSettings()
    const threshold = await screen.findByLabelText('文字检测置信度')
    fireEvent.change(threshold,{target:{value:'0.7'}})
    const main = document.querySelector<HTMLElement>('.settings-main')!
    main.scrollTop = 657
    await user.click(screen.getByRole('button',{name:'字体管理'}))
    expect(main.scrollTop).toBe(0)
    expect(screen.getByRole('heading',{level:1,name:'字体管理'})).toBeVisible()
    expect(threshold).not.toBeVisible()
    main.scrollTop = 240
    await user.click(screen.getByRole('button',{name:'编辑文字检测配置'}))
    expect(main.scrollTop).toBe(0)
    expect(threshold).toBeVisible()
    expect(threshold).toHaveValue('0.7')
    expect(api.models.list).toHaveBeenCalledTimes(1)
  })
  it('reveals the whole content on entry and category changes, without replaying while editing',async () => {
    compact = false
    const cancel = vi.fn()
    const animate = vi.fn(() => ({cancel}))
    Object.defineProperty(HTMLElement.prototype, 'animate', {configurable:true, value:animate})
    const user = userEvent.setup()
    const {unmount} = openSettings()
    const threshold = await screen.findByLabelText('文字检测置信度')
    const content = document.querySelector('.settings-content')!
    expect(animate).toHaveBeenCalledOnce()
    expect(animate.mock.contexts[0]).toBe(content)
    expect(animate).toHaveBeenLastCalledWith([{opacity:0}, {opacity:1}], expect.objectContaining({duration:220}))
    fireEvent.change(threshold,{target:{value:'0.7'}})
    expect(animate).toHaveBeenCalledOnce()
    await user.click(screen.getByRole('button',{name:'界面语言'}))
    await user.click(screen.getByRole('button',{name:'编辑文字检测配置'}))
    expect(animate).toHaveBeenCalledTimes(3)
    expect(animate.mock.contexts.every(target => target === content)).toBe(true)
    expect(screen.getByLabelText('文字检测置信度')).toBe(threshold)
    expect(threshold).toHaveValue('0.7')
    await user.click(screen.getByRole('button',{name:'编辑文字检测配置'}))
    expect(animate).toHaveBeenCalledTimes(3)
    expect(cancel).toHaveBeenCalledTimes(2)
    unmount()
    expect(cancel).toHaveBeenCalledTimes(3)
  })
  it('closes the mobile menu with Escape and returns focus to its trigger',async () => {
    const user = userEvent.setup()
    openSettings()
    await screen.findByLabelText('文字检测置信度')
    await user.click(screen.getByRole('button',{name:'设置'}))
    expect(screen.getByRole('button',{name:'设置'})).toHaveAttribute('aria-expanded','true')
    await user.keyboard('{Escape}')
    await waitFor(() => expect(screen.getByRole('button',{name:'设置'})).toHaveFocus())
    expect(document.querySelector('.settings-main')).not.toHaveAttribute('inert')
  })
  it('toggles the drawer from the existing topbar settings control without another toolbar',async () => {
    const user = userEvent.setup()
    openSettings()
    await screen.findByLabelText('文字检测置信度')
    const trigger = screen.getByRole('button',{name:'设置'})
    expect(document.querySelector('.settings-mobile-bar')).not.toBeInTheDocument()
    expect(document.querySelector('.settings-mobile-title')).not.toBeInTheDocument()
    expect(screen.queryByRole('button',{name:'设置菜单'})).not.toBeInTheDocument()
    expect(trigger.closest('header')).toBeInTheDocument()
    expect(trigger.querySelector('svg')).toHaveAttribute('aria-hidden','true')
    expect(trigger.querySelector('svg')).toHaveAttribute('width','17')
    expect(trigger.querySelector('svg')).toHaveAttribute('height','17')
    expect(trigger).toHaveAttribute('aria-controls','settings-navigation')
    expect(trigger).toHaveAttribute('aria-expanded','false')
    await user.click(trigger)
    expect(trigger).toHaveAttribute('aria-expanded','true')
    expect(document.querySelector('.settings-main')).toHaveAttribute('inert')
    expect(screen.getByRole('complementary',{name:'设置分类'})).toHaveClass('is-open')
    await user.click(trigger)
    expect(trigger).toHaveAttribute('aria-expanded','false')
    expect(document.querySelector('.settings-main')).not.toHaveAttribute('inert')
    await user.click(trigger)
    await user.click(screen.getByRole('button',{name:'编辑云端翻译配置'}))
    expect(trigger).toHaveAttribute('aria-expanded','false')
    expect(screen.getByRole('button',{name:'编辑云端翻译配置'})).toHaveAttribute('aria-current','page')
    expect(document.querySelector('.settings-main')).not.toHaveAttribute('inert')
    expect(api.models.list).toHaveBeenCalledTimes(1)
  })

  it('dismisses the drawer by its backdrop and restores focus to the topbar control',async () => {
    const user = userEvent.setup()
    openSettings()
    await screen.findByLabelText('文字检测置信度')
    const trigger = screen.getByRole('button',{name:'设置'})
    await user.click(trigger)
    await user.click(screen.getByRole('button',{name:'关闭设置菜单'}))
    expect(trigger).toHaveAttribute('aria-expanded','false')
    expect(trigger).toHaveFocus()
    expect(document.querySelector('.settings-main')).not.toHaveAttribute('inert')
  })

  it('keeps desktop navigation as a link and never reopens a stale drawer after resizing',async () => {
    const user = userEvent.setup()
    openSettings()
    await screen.findByLabelText('文字检测置信度')
    await user.click(screen.getByRole('button',{name:'设置'}))
    act(() => {compact=false;resizeListeners.forEach(listener => listener())})
    expect(screen.getByRole('link',{name:'设置'})).toHaveAttribute('href','/settings')
    expect(screen.queryByRole('button',{name:'关闭设置菜单'})).not.toBeInTheDocument()
    expect(document.querySelector('.settings-main')).not.toHaveAttribute('inert')
    act(() => {compact=true;resizeListeners.forEach(listener => listener())})
    expect(screen.getByRole('button',{name:'设置'})).toHaveAttribute('aria-expanded','false')
  })
})
