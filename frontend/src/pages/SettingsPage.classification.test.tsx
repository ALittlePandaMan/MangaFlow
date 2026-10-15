import {fireEvent, render, screen} from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import {beforeEach, describe, expect, it, vi} from 'vitest'
import {GlobalDialogProvider} from '../components/GlobalDialog'
import {api} from '../services/api'
import type {ModelConfiguration, ModelDescriptor} from '../types'
import {SettingsPage} from './SettingsPage'

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
  it('edits unified RF-DETR thresholds without creating a second classifier or disabling layout detection', async () => {
    const rfdetr={...descriptor,name:'rfdetr',supports_batch:false,description:'Unified layout'}
    const configured={...configuration,provider:'rfdetr',name:'RF-DETR',capabilities:rfdetr,config:{device:'cpu',text_threshold:0.25,bubble_threshold:0.5,panel_threshold:0.5,containment_threshold:0.9,text_classification:{filter_sfx:true,min_sfx_confidence:0.6}}}
    vi.mocked(api.models.list).mockResolvedValue({available:[rfdetr],configured:[configured]})
    const user=userEvent.setup()
    render(<GlobalDialogProvider><SettingsPage/></GlobalDialogProvider>)
    await user.click(await screen.findByRole('button',{name:'编辑文字检测配置'}))
    fireEvent.change(screen.getByLabelText('文字检测置信度'),{target:{value:'0.3'}})
    fireEvent.change(screen.getByLabelText('文字气泡包含率'),{target:{value:'0.95'}})
    expect(screen.queryByRole('button',{name:'拟声词处理'})).not.toBeInTheDocument()
    await user.click(screen.getByRole('button',{name:'保存配置'}))
    expect(api.models.update).toHaveBeenCalledWith('detection-1',expect.objectContaining({config:expect.objectContaining({text_threshold:0.3,containment_threshold:0.95})}))
    expect(vi.mocked(api.models.update).mock.calls[0][1].config).not.toHaveProperty('text_classification')
  })
})
