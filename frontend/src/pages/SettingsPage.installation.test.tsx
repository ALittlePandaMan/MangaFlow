import {act, fireEvent, render, screen, waitFor, within} from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import {beforeEach, describe, expect, it, vi} from 'vitest'
import {GlobalDialogProvider} from '../components/GlobalDialog'
import {api, type RecommendedSetupStatus} from '../services/api'
import type {ModelDescriptor} from '../types'
import {SettingsPage} from './SettingsPage'

vi.mock('../components/LoadingUI', async original => ({...await original<typeof import('../components/LoadingUI')>(),useMinimumLoadingTime:(value:boolean) => value}))
const missing: RecommendedSetupStatus = {state:'idle',installed:false,configured:false,error:null,models:[]}
const installed: RecommendedSetupStatus = {...missing,state:'complete',installed:true,configured:true}

beforeEach(() => {
  vi.restoreAllMocks()
  const descriptor = {kind:'detection',name:'rfdetr',devices:['cpu','cuda'],description:'RF-DETR',supports_batch:false,installed:true,orientations:[],capabilities:{},is_fallback:false} as ModelDescriptor
  vi.spyOn(api.models,'list').mockResolvedValue({available:[descriptor],configured:[]})
  vi.spyOn(api.fonts,'list').mockResolvedValue([])
  vi.spyOn(api.models,'recommendedStatus').mockResolvedValue(missing)
  vi.spyOn(api.models,'installRecommended').mockResolvedValue(installed)
  vi.spyOn(api.models,'bootstrap').mockRejectedValue(new Error('The wizard must not be used'))
})

async function open() {
  const user = userEvent.setup()
  const view = render(<GlobalDialogProvider><SettingsPage/></GlobalDialogProvider>)
  await waitFor(() => expect(screen.getByRole('button',{name:'安装推荐配置'})).toBeEnabled())
  const button = screen.getByRole('button',{name:'安装推荐配置'})
  return {user,button,...view}
}

describe('recommended local setup', () => {
  it('does not install before confirmation and does nothing on cancel',async () => {
    const {user,button} = await open()
    await user.click(button)
    expect(await screen.findByRole('dialog')).toHaveTextContent('是否下载并安装')
    expect(api.models.installRecommended).not.toHaveBeenCalled()
    await user.click(screen.getByRole('button',{name:'取消'}))
    expect(api.models.installRecommended).not.toHaveBeenCalled()
    expect(api.models.bootstrap).not.toHaveBeenCalled()
    expect(button).toBeEnabled()
  })

  it('installs the fixed set on confirmation and shows a short completion notice',async () => {
    const {user,button} = await open()
    await user.click(button)
    await user.click(await screen.findByRole('button',{name:'确认安装'}))
    const dialog=await screen.findByRole('status',{name:'安装推荐配置'})
    expect(dialog).toHaveTextContent('配置已安装')
    expect(document.querySelector('.settings-main .settings-notice')).toBeNull()
    expect(api.models.installRecommended).toHaveBeenCalledTimes(1)
    expect(api.models.installRecommended).toHaveBeenCalledWith()
    expect(api.models.bootstrap).not.toHaveBeenCalled()
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    await user.click(within(dialog).getByRole('button',{name:'关闭提示'}))
    expect(screen.queryByText('配置已安装')).not.toBeInTheDocument()
  })

  it('only notifies when assets and configurations already exist, even after an old job error',async () => {
    vi.mocked(api.models.recommendedStatus).mockResolvedValue({...installed,state:'error',error:'old failure'})
    const {user,button} = await open()
    await user.click(button)
    expect(await screen.findByText('配置已安装')).toBeVisible()
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    expect(api.models.installRecommended).not.toHaveBeenCalled()
  })

  it('configures downloaded assets without a download confirmation',async () => {
    vi.mocked(api.models.recommendedStatus).mockResolvedValue({...installed,configured:false})
    const {user,button} = await open()
    await user.click(button)
    expect(await screen.findByText('配置已安装')).toBeVisible()
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    expect(api.models.installRecommended).toHaveBeenCalledTimes(1)
  })

  it('reports an offline status failure and permits a retry without installing anything',async () => {
    vi.mocked(api.models.recommendedStatus).mockRejectedValueOnce(new Error('无法读取安装状态'))
    const {user,button} = await open()
    await user.click(button)
    const dialog=await screen.findByRole('alert',{name:'安装失败'})
    expect(dialog).toHaveTextContent('无法读取安装状态')
    expect(button).toBeEnabled()
    expect(api.models.installRecommended).not.toHaveBeenCalled()
    await user.click(within(dialog).getByRole('button',{name:'关闭提示'}))
    await user.click(button)
    expect(await screen.findByRole('dialog')).toBeVisible()
  })

  it('shows installation failures without reporting success',async () => {
    vi.mocked(api.models.installRecommended).mockResolvedValue({...missing,state:'error',error:'下载失败，请重试'})
    const {user,button} = await open()
    await user.click(button)
    await user.click(await screen.findByRole('button',{name:'确认安装'}))
    expect(await screen.findByRole('alert',{name:'安装失败'})).toHaveTextContent('下载失败，请重试')
    expect(screen.queryByText('配置已安装')).not.toBeInTheDocument()
    expect(button).toBeEnabled()
  })

  it('deduplicates rapid clicks during the installation check',async () => {
    let finish!: (status: RecommendedSetupStatus) => void
    vi.mocked(api.models.recommendedStatus).mockReturnValueOnce(new Promise(resolve => {finish=resolve}))
    const {button} = await open()
    fireEvent.click(button)
    fireEvent.click(button)
    expect(api.models.recommendedStatus).toHaveBeenCalledTimes(1)
    await act(async () => {finish(installed)})
    expect(await screen.findByText('配置已安装')).toBeVisible()
  })

  it('rejoins a running backend job instead of opening another confirmation or starting another job',async () => {
    vi.mocked(api.models.recommendedStatus).mockResolvedValueOnce({...missing,state:'installing'}).mockResolvedValue(installed)
    const {user,button} = await open()
    await user.click(button)
    expect(button).toBeDisabled()
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    expect(api.models.installRecommended).not.toHaveBeenCalled()
    await waitFor(() => expect(screen.getByText('配置已安装')).toBeVisible(),{timeout:2500})
    expect(button).toBeEnabled()
  })

  it('does not open a late confirmation after leaving settings',async () => {
    let finish!: (status: RecommendedSetupStatus) => void
    vi.mocked(api.models.recommendedStatus).mockReturnValueOnce(new Promise(resolve => {finish=resolve}))
    const {user,button,unmount} = await open()
    await user.click(button)
    unmount()
    await act(async () => {finish(missing)})
    expect(api.models.installRecommended).not.toHaveBeenCalled()
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  })
})
