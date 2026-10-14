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

const descriptor: ModelDescriptor = {kind: 'detection', name: 'paddleocr', description: 'PaddleOCR', devices: ['cpu', 'cuda'], orientations: ['vertical'], supports_batch: true, capabilities: {}, installed: true, is_fallback: false}
const configuration: ModelConfiguration = {id: 'detection-1', kind: 'detection', provider: 'paddleocr', name: 'PaddleOCR', enabled: true, is_default: true, has_api_key: false, config: {device: 'cpu', language: 'japan', box_threshold: 0.45, text_classification: {filter_sfx: false, min_sfx_confidence: 0.75}}, capabilities: {...descriptor}}

beforeEach(() => {
  vi.restoreAllMocks()
  vi.spyOn(api.models, 'list').mockResolvedValue({available: [descriptor], configured: [configuration]})
  vi.spyOn(api.fonts, 'list').mockResolvedValue([])
  vi.spyOn(api.models, 'update').mockResolvedValue(configuration)
})

describe('classification model form', () => {
  it('saves classification settings without losing detection parameters', async () => {
    const user = userEvent.setup()
    render(<GlobalDialogProvider><SettingsPage/></GlobalDialogProvider>)
    await user.click(await screen.findByRole('button', {name: '编辑文字检测配置'}))
    expect(screen.getByRole('button', {name: '拟声词处理'})).toHaveTextContent('分类但保留拟声词')
    await user.click(screen.getByRole('button', {name: '拟声词处理'}))
    await user.click(screen.getByRole('option', {name: '分类并过滤拟声词'}))
    fireEvent.change(screen.getByLabelText('拟声词过滤置信度'), {target: {value: '0.8'}})
    await user.click(screen.getByRole('button', {name: '保存配置'}))
    expect(api.models.update).toHaveBeenCalledWith('detection-1', expect.objectContaining({config: expect.objectContaining({language: 'japan', box_threshold: 0.45, text_classification: {enabled: true, filter_sfx: true, min_sfx_confidence: 0.8}})}))
  })
})
