import {act, fireEvent, render, screen, waitFor, within} from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import {beforeEach, describe, expect, it, vi} from 'vitest'
import {GlobalDialogProvider} from '../components/GlobalDialog'
import {api} from '../services/api'
import type {ModelConfiguration, ModelDescriptor} from '../types'
import {SettingsPage} from './SettingsPage'
import {changeLocale, t} from '../i18n'

vi.mock('../components/LoadingUI', async importOriginal => ({
  ...await importOriginal<typeof import('../components/LoadingUI')>(),
  useMinimumLoadingTime: (value: boolean) => value,
}))

const descriptor: ModelDescriptor = {kind: 'detection', name: 'rfdetr', description: 'RF-DETR', devices: ['cpu', 'cuda'], orientations: ['vertical'], supports_batch: true, capabilities: {}, installed: true, is_fallback: false}
const configuration: ModelConfiguration = {id: 'detection-1', kind: 'detection', provider: 'rfdetr', name: 'RF-DETR', enabled: true, is_default: true, has_api_key: false, config: {device: 'cpu', text_classification: {filter_sfx: false, min_sfx_confidence: 0.75}}, capabilities: {...descriptor}}

beforeEach(() => {
  vi.restoreAllMocks()
  vi.spyOn(api.models, 'list').mockResolvedValue({available: [descriptor], configured: [configuration]})
  vi.spyOn(api.fonts, 'list').mockResolvedValue([])
  vi.spyOn(api.models, 'update').mockResolvedValue(configuration)
})

describe('classification model form', () => {
  it.each(['zh-CN','zh-TW','en','ja'] as const)('shows a localized success toast after model settings are saved in %s',async locale => {
    changeLocale(locale)
    const user=userEvent.setup()
    render(<GlobalDialogProvider><SettingsPage/></GlobalDialogProvider>)
    await screen.findByLabelText(t('文字检测置信度'))
    await user.click(screen.getByRole('button',{name:t('保存配置')}))
    const dialog=await screen.findByRole('status',{name:t('保存成功')})
    expect(dialog).toHaveTextContent(t('{{v0}}配置已保存。',{v0:t('文字检测')}))
    expect(screen.getAllByRole('status',{name:t('保存成功')})).toHaveLength(1)
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    expect(document.querySelector('.settings-main .settings-notice')).toBeNull()
    await user.click(within(dialog).getByRole('button',{name:t('关闭提示')}))
    expect(screen.queryByRole('status',{name:t('保存成功')})).not.toBeInTheDocument()
    expect(screen.queryByText(t('{{v0}}配置已保存。',{v0:t('文字检测')}))).not.toBeInTheDocument()
    expect(screen.getByRole('button',{name:t('保存配置')})).toBeEnabled()
  })

  it('waits for the save request before showing success and blocks repeated clicks',async () => {
    let finish!:(value:ModelConfiguration)=>void
    vi.mocked(api.models.update).mockReturnValue(new Promise(resolve=>{finish=resolve}))
    const user=userEvent.setup()
    render(<GlobalDialogProvider><SettingsPage/></GlobalDialogProvider>)
    await screen.findByLabelText('文字检测置信度')
    await user.click(screen.getByRole('button',{name:'保存配置'}))
    expect(screen.queryByRole('status',{name:'保存成功'})).not.toBeInTheDocument()
    const saving=screen.getByRole('button',{name:'正在保存…'})
    expect(saving).toBeDisabled()
    await user.click(saving)
    expect(api.models.update).toHaveBeenCalledTimes(1)
    await act(async()=>{finish(configuration)})
    expect(await screen.findByRole('status',{name:'保存成功'})).toBeVisible()
  })

  it('reports model save errors without clearing the edited configuration and permits retry',async () => {
    vi.mocked(api.models.update).mockRejectedValueOnce(new Error('服务暂不可用')).mockResolvedValue(configuration)
    const user=userEvent.setup()
    render(<GlobalDialogProvider><SettingsPage/></GlobalDialogProvider>)
    const threshold=await screen.findByLabelText('文字检测置信度')
    fireEvent.change(threshold,{target:{value:'0.42'}})
    await user.click(screen.getByRole('button',{name:'保存配置'}))
    const dialog=await screen.findByRole('alert',{name:'保存失败'})
    expect(dialog).toHaveTextContent('服务暂不可用')
    expect(document.querySelector('.settings-form .settings-error')).toBeNull()
    expect(screen.queryByRole('status',{name:'保存成功'})).not.toBeInTheDocument()
    await user.click(within(dialog).getByRole('button',{name:'关闭提示'}))
    expect(threshold).toHaveValue('0.42')
    await user.click(screen.getByRole('button',{name:'保存配置'}))
    expect(await screen.findByRole('status',{name:'保存成功'})).toBeVisible()
    expect(api.models.update).toHaveBeenCalledTimes(2)
  })

  it('switches languages from settings and retains other unsaved settings', async () => {
    render(<GlobalDialogProvider><SettingsPage/></GlobalDialogProvider>)
    await screen.findByRole('button',{name:'编辑文字检测配置'})
    fireEvent.change(screen.getByRole('textbox',{name:'文字检测置信度'}),{target:{value:'0.31'}})
    fireEvent.click(within(screen.getByRole('navigation',{name:'设置分类导航'})).getByRole('button',{name:'界面语言'}))
    expect(screen.getByRole('heading',{name:'界面语言',level:1})).toBeVisible()
    const selector=screen.getAllByRole('button',{name:'界面语言'}).find(button => button.getAttribute('aria-haspopup') === 'listbox')!
    fireEvent.click(selector)
    fireEvent.click(screen.getByRole('option',{name:'English'}))
    expect(screen.getByRole('heading',{name:'Interface language',level:1})).toBeVisible()
    fireEvent.click(screen.getByRole('button',{name:'Configure Text detection'}))
    expect(screen.getByRole('textbox',{name:'Text confidence'})).toHaveValue('0.31')
    expect(api.models.update).not.toHaveBeenCalled()
  })

  it('saves four independent margins while preserving legacy uniform padding', async () => {
    const rfdetr={...descriptor,name:'rfdetr',supports_batch:false,description:'Unified layout'}
    const configured={...configuration,provider:'rfdetr',name:'RF-DETR',capabilities:rfdetr,config:{device:'cpu',text_threshold:0.25,bubble_threshold:0.5,panel_threshold:0.5,containment_threshold:0.9,text_box_padding:6,text_classification:{filter_sfx:true,min_sfx_confidence:0.6}}}
    vi.mocked(api.models.list).mockResolvedValue({available:[rfdetr],configured:[configured]})
    const user=userEvent.setup()
    render(<GlobalDialogProvider><SettingsPage/></GlobalDialogProvider>)
    await waitFor(() => expect(screen.getByRole('button',{name:'编辑文字检测配置'})).toBeEnabled())
    await user.click(screen.getByRole('button',{name:'编辑文字检测配置'}))
    const margins={top:0,bottom:8,left:12,right:4}
    for(const [side,label] of [['top','上'],['bottom','下'],['left','左'],['right','右']] as const) {
      const input=screen.getByRole('textbox',{name:`文本框${label}扩边像素`})
      expect(input).toHaveValue('6')
      fireEvent.change(input,{target:{value:String(margins[side])}})
    }
    fireEvent.change(screen.getByLabelText('文字检测置信度'),{target:{value:'0.3'}})
    fireEvent.change(screen.getByLabelText('文字气泡包含率'),{target:{value:'0.95'}})
    expect(screen.queryByRole('button',{name:'拟声词处理'})).not.toBeInTheDocument()
    await user.click(screen.getByRole('button',{name:'保存配置'}))
    expect(api.models.update).toHaveBeenCalledWith('detection-1',expect.objectContaining({config:expect.objectContaining({text_threshold:0.3,containment_threshold:0.95,text_box_padding:margins})}))
    expect(vi.mocked(api.models.update).mock.calls[0][1].config).not.toHaveProperty('text_classification')
  })

  it('defaults an existing configuration without padding to 3px',async () => {
    const user=userEvent.setup()
    render(<GlobalDialogProvider><SettingsPage/></GlobalDialogProvider>)
    await user.click(await screen.findByRole('button',{name:'编辑文字检测配置'}))
    for(const side of ['上','下','左','右']) expect(screen.getByRole('textbox',{name:`文本框${side}扩边像素`})).toHaveValue('3')
    await user.click(screen.getByRole('button',{name:'保存配置'}))
    expect(api.models.update).toHaveBeenCalledWith('detection-1',expect.objectContaining({config:expect.objectContaining({text_box_padding:{top:3,bottom:3,left:3,right:3}})}))
  })

  it('loads saved per-edge margins including zero',async () => {
    vi.mocked(api.models.list).mockResolvedValue({available:[descriptor],configured:[{...configuration,
      config:{device:'cpu',text_box_padding:{top:0,bottom:5,left:10,right:20}}}]})
    const user=userEvent.setup()
    render(<GlobalDialogProvider><SettingsPage/></GlobalDialogProvider>)
    await user.click(await screen.findByRole('button',{name:'编辑文字检测配置'}))
    for(const [label,value] of [['上',0],['下',5],['左',10],['右',20]] as const) {
      expect(screen.getByRole('textbox',{name:`文本框${label}扩边像素`})).toHaveValue(String(value))
    }
  })
})
