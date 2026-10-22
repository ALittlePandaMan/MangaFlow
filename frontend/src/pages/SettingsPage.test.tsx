import {act, fireEvent, render, screen, waitFor, within} from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import {beforeEach, describe, expect, it, vi} from 'vitest'
import {GlobalDialogProvider} from '../components/GlobalDialog'
import {DEFAULT_OCR_PROMPT} from '../constants/ocr'
import {api} from '../services/api'
import type {ModelConfiguration, ModelDescriptor} from '../types'
import {SettingsPage} from './SettingsPage'

vi.mock('../components/LoadingUI', async importOriginal => ({
  ...await importOriginal<typeof import('../components/LoadingUI')>(),
  useMinimumLoadingTime: (value: boolean) => value,
}))

const descriptors = ['manga-ocr', 'cloud-multimodal', 'cloud-ocr', 'paddleocr-vl-1.6', 'baberu-ocr', 'hayai-ocr', 'paddleocr', 'review-fallback'].map(name => ({
  kind: 'ocr', name, description: name, devices: ['cloud-multimodal', 'cloud-ocr'].includes(name) ? ['remote'] : ['cpu', 'cuda'],
  orientations: ['vertical'], supports_batch: false, capabilities: {}, installed: true, is_fallback: false,
})) as ModelDescriptor[]
let configuration: ModelConfiguration
let translation: ModelConfiguration | undefined
let savedOcr: ModelConfiguration | undefined

beforeEach(() => {
  vi.restoreAllMocks()
  translation = undefined
  savedOcr = undefined
  configuration = {id: 'ocr-1', kind: 'ocr', provider: 'manga-ocr', name: 'MangaOCR', enabled: true, is_default: true, has_api_key: false, config: {device: 'cpu', model: 'local-manga'}, capabilities: {...descriptors[0]}}
  vi.spyOn(api.models, 'list').mockImplementation(async () => ({available: descriptors, configured: [configuration, ...(translation ? [translation] : []), ...(savedOcr ? [savedOcr] : [])]}))
  vi.spyOn(api.fonts, 'list').mockResolvedValue([])
  vi.spyOn(api.models, 'update').mockResolvedValue(configuration)
  vi.spyOn(api.models, 'create').mockResolvedValue(configuration)
  vi.spyOn(api.models, 'discover').mockResolvedValue({models: ['vision-test'], base_url: 'https://vision.example/v1', endpoint: 'https://vision.example/v1/models', protocol: 'openai'})
})

async function openCloud() {
  const user = userEvent.setup()
  render(<GlobalDialogProvider><SettingsPage/></GlobalDialogProvider>)
  await waitFor(() => expect(screen.getByRole('button',{name:'编辑OCR 识别配置'})).toBeEnabled())
  await user.click(screen.getByRole('button',{name:'编辑OCR 识别配置'}))
  if (configuration.provider !== 'cloud-multimodal') {
    await user.click(screen.getByRole('button', {name: '当前模型'}))
    await user.click(screen.getByRole('option', {name: '多模态大模型识字'}))
  }
  return user
}

describe('cloud multimodal OCR settings', () => {
  it('tests a reused translation connection without saving or exposing its key',async()=>{
    translation={...configuration,id:'translation',kind:'translation',provider:'openai-compatible',has_api_key:true,
      config:{base_url:'https://vision.example/v1',model:'vision-test',api_protocol:'responses'}}
    const probe=vi.spyOn(api.models,'testConnection').mockResolvedValue({ok:true,mode:'remote',elapsed_ms:10})
    const user=await openCloud()
    await user.click(screen.getByRole('button',{name:'测试连接'}))
    expect(probe).toHaveBeenCalledWith(expect.objectContaining({kind:'ocr',provider:'cloud-multimodal',reuse_translation:true,api_key:undefined}))
    const request=probe.mock.calls[0][0]
    expect(request.config).not.toHaveProperty('api_key')
    expect(request.config).not.toHaveProperty('base_url')
    expect(await screen.findByText('连接测试通过')).toBeVisible()
    expect(api.models.update).not.toHaveBeenCalled()
    expect(api.models.create).not.toHaveBeenCalled()
  })

  it('offers automatic GPU priority and saves the policy rather than the current hardware', async () => {
    const user = userEvent.setup()
    render(<GlobalDialogProvider><SettingsPage/></GlobalDialogProvider>)
    await waitFor(() => expect(screen.getByRole('button',{name:'编辑OCR 识别配置'})).toBeEnabled())
    await user.click(screen.getByRole('button',{name:'编辑OCR 识别配置'}))
    await user.click(screen.getByRole('button', {name: '运行设备'}))
    await user.click(screen.getByRole('option', {name: '自动（GPU 优先）'}))
    await user.click(screen.getByRole('button', {name: '保存配置'}))
    expect(api.models.update).toHaveBeenCalledWith('ocr-1', expect.objectContaining({
      config: {device: 'auto', model: 'local-manga'},
    }))
  })
  it('restores an automatic device configuration without converting it to fixed GPU or CPU', async () => {
    configuration = {...configuration, config: {device: 'auto', model: 'local-manga'}}
    const user = userEvent.setup()
    render(<GlobalDialogProvider><SettingsPage/></GlobalDialogProvider>)
    await waitFor(() => expect(screen.getByRole('button',{name:'编辑OCR 识别配置'})).toBeEnabled())
    await user.click(screen.getByRole('button',{name:'编辑OCR 识别配置'}))
    expect(screen.getByRole('button', {name: '运行设备'})).toHaveTextContent('自动（GPU 优先）')
    await user.click(screen.getByRole('button', {name: '保存配置'}))
    expect(api.models.update).toHaveBeenCalledWith('ocr-1', expect.objectContaining({
      config: {device: 'auto', model: 'local-manga'},
    }))
  })
  it('switches to Koharu OCR with independent local generation settings and preserves MangaOCR', async () => {
    const user = await openCloud()
    await user.click(screen.getByRole('button', {name: '当前模型'}))
    await user.click(screen.getByRole('option', {name: 'PaddleOCR-VL 1.6'}))
    expect(screen.queryByLabelText('API Key')).not.toBeInTheDocument()
    expect(screen.getByLabelText('本地 OCR 最大输出 Token 数')).toHaveValue('2048')
    expect(screen.getByRole('button',{name:'本地 OCR 最大输出 Token 数增大'})).toBeDisabled()
    fireEvent.change(screen.getByLabelText('本地 OCR 最大输出 Token 数'), {target: {value: '768'}})
    await user.click(screen.getByRole('button', {name: '保存配置'}))
    expect(api.models.update).not.toHaveBeenCalled()
    expect(api.models.create).toHaveBeenCalledWith(expect.objectContaining({provider: 'paddleocr-vl-1.6', is_default: true,
      config: {device: 'auto', max_new_tokens: 768, timeout: 180}}))
  })
  it('resets generation settings when switching between Baberu and Hayai', async () => {
    const user = await openCloud()
    await user.click(screen.getByRole('button', {name: '当前模型'}))
    await user.click(screen.getByRole('option', {name: 'Baberu OCR'}))
    fireEvent.change(screen.getByLabelText('本地 OCR 最大输出 Token 数'), {target: {value: '512'}})
    await user.click(screen.getByRole('button', {name: '当前模型'}))
    await user.click(screen.getByRole('option', {name: 'Hayai OCR'}))
    expect(screen.getByLabelText('本地 OCR 最大输出 Token 数')).toHaveValue('2048')
    await user.click(screen.getByRole('button', {name: '保存配置'}))
    expect(api.models.create).toHaveBeenCalledWith(expect.objectContaining({provider: 'hayai-ocr', config: expect.objectContaining({max_new_tokens: 2048})}))
  })
  it('restores and updates the saved provider instead of overwriting the local OCR config', async () => {
    savedOcr = {...configuration, id: 'saved-cloud', provider: 'cloud-multimodal', is_default: false, has_api_key: true, config: {model: 'saved-vision', base_url: 'https://vision.example/v1', prompt: 'Saved OCR prompt'}}
    const user = await openCloud()
    expect(screen.getByLabelText('识别 Prompt')).toHaveValue('Saved OCR prompt')
    expect(screen.getByLabelText('API Key')).not.toBeRequired()
    const keyInput = screen.getByLabelText('API Key')
    expect(keyInput).toHaveValue('')
    expect(keyInput).toHaveAttribute('placeholder','••••••••')
    expect(screen.queryByText('密钥已保存')).not.toBeInTheDocument()
    await user.click(keyInput)
    expect(keyInput).toHaveAttribute('placeholder','')
    fireEvent.change(screen.getByLabelText('OCR 并发上限'), {target:{value:'5'}})
    fireEvent.change(screen.getByLabelText('OCR 每分钟请求上限'), {target:{value:'90'}})
    await user.click(screen.getByRole('button', {name: '保存配置'}))
    expect(api.models.create).not.toHaveBeenCalled()
    expect(api.models.update).toHaveBeenCalledWith('saved-cloud', expect.objectContaining({provider: 'cloud-multimodal', config: expect.objectContaining({model: 'saved-vision', concurrency_mode:'adaptive', max_concurrency:5, requests_per_minute:90})}))
    expect(vi.mocked(api.models.update).mock.calls[0][1].api_key).toBeUndefined()
  })
  it('reuses translation credentials server-side while retaining a separate OCR prompt', async () => {
    translation = {...configuration, id: 'translation-1', kind: 'translation', provider: 'openai-compatible', has_api_key: true, config: {base_url: 'https://translation.example/v1', model: 'gemini-3.8-flash-high', prompt: 'Translate, do not transcribe'}}
    const user = await openCloud()
    expect(screen.getByRole('button', {name: '连接配置'})).toHaveTextContent('使用当前翻译模型')
    expect(screen.queryByLabelText('API Key')).not.toBeInTheDocument()
    expect(screen.getByLabelText('识别 Prompt')).toHaveValue(DEFAULT_OCR_PROMPT)
    expect(api.models.discover).not.toHaveBeenCalled()
    await user.click(screen.getByRole('button', {name: '保存配置'}))
    expect(api.models.create).toHaveBeenCalledWith(expect.objectContaining({provider: 'cloud-multimodal', reuse_translation: true, api_key: undefined, config: expect.objectContaining({prompt: DEFAULT_OCR_PROMPT})}))
    expect(vi.mocked(api.models.create).mock.calls[0][0].config).not.toHaveProperty('base_url')
  })

  it('removes classic PaddleOCR and generic cloud OCR while retaining PaddleOCR-VL', async () => {
    const user = await openCloud()
    await user.click(screen.getByRole('button', {name: '当前模型'}))
    expect(screen.queryByRole('option', {name: '云端 OCR（通用接口）'})).not.toBeInTheDocument()
    expect(screen.queryByRole('option', {name: 'PaddleOCR 文字识别'})).not.toBeInTheDocument()
    expect(screen.getByRole('option', {name: 'PaddleOCR-VL 1.6'})).toBeVisible()
    expect(screen.getByRole('option', {name: '多模态大模型识字'})).toBeVisible()
  })

  it('keeps a saved model and prompt when discovery fails without offering manual entry', async () => {
    configuration = {...configuration, provider:'cloud-multimodal', has_api_key:true, config:{base_url:'https://vision.example/v1',model:'saved-vision'}}
    vi.mocked(api.models.discover).mockRejectedValue(new Error('HTTP 404'))
    const user = await openCloud()
    expect(screen.queryByRole('button', {name: '运行设备'})).not.toBeInTheDocument()
    expect(screen.getByLabelText('识别 Prompt')).toHaveValue(DEFAULT_OCR_PROMPT)
    expect(screen.getByText(/图片会发送到你配置的服务/)).toBeVisible()
    fireEvent.change(screen.getByLabelText('API Base URL'), {target: {value: 'https://vision.example/v1'}})
    fireEvent.change(screen.getByLabelText('API Key'), {target: {value: 'new-secret'}})
    await user.click(screen.getByRole('button', {name: '重新获取模型列表'}))
    const dialog=await screen.findByRole('alert',{name:'获取模型'})
    expect(dialog).toHaveTextContent('HTTP 404')
    expect(document.querySelector('.settings-model-picker .settings-error')).toBeNull()
    await user.click(within(dialog).getByRole('button',{name:'关闭提示'}))
    expect(screen.queryByRole('button', {name: '手动输入模型'})).not.toBeInTheDocument()
    expect(screen.getByRole('button', {name: '选择 OCR 模型'})).toHaveTextContent('saved-vision')
    fireEvent.change(screen.getByLabelText('识别 Prompt'), {target: {value: '保留日文标点，不翻译'}})
    await user.click(screen.getByRole('button', {name: '保存配置'}))
    await waitFor(() => expect(api.models.update).toHaveBeenCalledWith('ocr-1',expect.objectContaining({
      kind: 'ocr', provider: 'cloud-multimodal', api_key: 'new-secret', name: '多模态识字 · saved-vision',
      config: expect.objectContaining({model: 'saved-vision', prompt: '保留日文标点，不翻译', max_tokens: 2048}),
    })))
    const payload = vi.mocked(api.models.update).mock.calls[0][1]
    expect(api.models.create).not.toHaveBeenCalled()
    expect(payload.config).not.toHaveProperty('device')
    expect(payload.config).not.toHaveProperty('temperature')
  })

  it('discovers models without silently selecting a potentially text-only model', async () => {
    const user = await openCloud()
    fireEvent.change(screen.getByLabelText('API Base URL'), {target: {value: 'https://vision.example/v1'}})
    fireEvent.change(screen.getByLabelText('API Key'), {target: {value: 'ocr-key'}})
    await user.click(screen.getByRole('button', {name: '重新获取模型列表'}))
    await waitFor(() => expect(screen.getByRole('button', {name: '选择 OCR 模型'})).toBeEnabled())
    const dialog=await screen.findByRole('status',{name:'获取模型'})
    expect(dialog).toHaveTextContent('已获取 1 个模型')
    expect(document.querySelector('.settings-model-picker .settings-notice')).toBeNull()
    await user.click(within(dialog).getByRole('button',{name:'关闭提示'}))
    expect(screen.getByRole('button', {name: '选择 OCR 模型'})).toHaveTextContent('请选择模型')
    await user.click(screen.getByRole('button', {name: '选择 OCR 模型'}))
    await user.click(screen.getByRole('option', {name: 'vision-test'}))
    await user.click(screen.getByRole('button', {name: '保存配置'}))
    expect(api.models.create).toHaveBeenCalledWith(expect.objectContaining({config: expect.objectContaining({model: 'vision-test', api_protocol: 'openai'})}))
  })

  it('requires a discovered model after failure instead of offering manual entry',async()=>{
    vi.mocked(api.models.discover).mockRejectedValue(new Error('模型列表获取失败'))
    const user = await openCloud()
    fireEvent.change(screen.getByLabelText('API Base URL'), {target:{value:'https://vision.example/v1'}})
    fireEvent.change(screen.getByLabelText('API Key'), {target:{value:'synthetic-key'}})
    await user.click(screen.getByRole('button',{name:'重新获取模型列表'}))
    const dialog=await screen.findByRole('alert',{name:'获取模型'})
    expect(dialog).toHaveTextContent('模型列表获取失败')
    await user.click(within(dialog).getByRole('button',{name:'关闭提示'}))
    expect(screen.queryByRole('button',{name:'手动输入模型'})).not.toBeInTheDocument()
    expect(screen.getByRole('button',{name:'选择 OCR 模型'})).toBeDisabled()
    await user.click(screen.getByRole('button',{name:'保存配置'}))
    expect(screen.getByText('请先获取并选择模型。')).toBeVisible()
    expect(api.models.create).not.toHaveBeenCalled()
    expect(api.models.update).not.toHaveBeenCalled()
  })

  it('preserves a saved key but requires re-entry after changing the API origin', async () => {
    configuration = {...configuration, provider: 'cloud-multimodal', has_api_key: true, config: {base_url: 'https://vision.example/v1', model: 'saved-vision', prompt: 'custom OCR'}}
    await openCloud()
    expect(screen.getByLabelText('API Key')).not.toBeRequired()
    expect(screen.getByLabelText('识别 Prompt')).toHaveValue('custom OCR')
    fireEvent.change(screen.getByLabelText('API Base URL'), {target: {value: 'https://other.example/v1'}})
    expect(screen.getByLabelText('API Key')).toBeRequired()
    expect(screen.getByLabelText('API Key')).toHaveAttribute('placeholder','输入 API 密钥')
    fireEvent.blur(screen.getByLabelText('API Base URL'))
    expect(api.models.discover).not.toHaveBeenCalled()
  })

  it.each(['cloud-ocr','review-fallback'])('does not expose or overwrite a retired %s OCR configuration',async provider => {
    configuration = {...configuration,provider,has_api_key:provider === 'cloud-ocr',config:provider === 'cloud-ocr' ? {base_url:'https://ocr.example/recognize',auth_mode:'bearer',request_format:'json'} : {}}
    const user = userEvent.setup()
    render(<GlobalDialogProvider><SettingsPage/></GlobalDialogProvider>)
    await waitFor(() => expect(screen.getByRole('button',{name:'编辑OCR 识别配置'})).toBeEnabled())
    await user.click(screen.getByRole('button',{name:'编辑OCR 识别配置'}))
    expect(screen.getByRole('button',{name:'当前模型'})).toHaveTextContent('PaddleOCR-VL 1.6')
    expect(screen.queryByLabelText('API Key')).not.toBeInTheDocument()
    expect(api.models.create).not.toHaveBeenCalled()
    expect(api.models.update).not.toHaveBeenCalled()
    await user.click(screen.getByRole('button',{name:'当前模型'}))
    expect(screen.queryByRole('option',{name:'人工审核占位'})).not.toBeInTheDocument()
    expect(screen.queryByRole('option',{name:'review-fallback'})).not.toBeInTheDocument()
    expect(screen.getByRole('option',{name:'PaddleOCR-VL 1.6'})).toBeVisible()
    await user.click(screen.getByRole('option',{name:'PaddleOCR-VL 1.6'}))
    await user.click(screen.getByRole('button',{name:'保存配置'}))
    await waitFor(() => expect(api.models.create).toHaveBeenCalledWith(expect.objectContaining({provider:'paddleocr-vl-1.6',config:expect.objectContaining({max_new_tokens:2048})})))
    expect(api.models.update).not.toHaveBeenCalled()
  })

  it('ignores stale discovery after switching back to local OCR', async () => {
    let resolve!: (value: Awaited<ReturnType<typeof api.models.discover>>) => void
    vi.mocked(api.models.discover).mockReturnValue(new Promise(done => {resolve = done}))
    const user = await openCloud()
    fireEvent.change(screen.getByLabelText('API Base URL'), {target: {value: 'https://vision.example/v1'}})
    fireEvent.change(screen.getByLabelText('API Key'), {target: {value: 'key'}})
    await user.click(screen.getByRole('button', {name: '重新获取模型列表'}))
    await user.click(screen.getByRole('button', {name: '当前模型'}))
    await user.click(screen.getByRole('option', {name: 'MangaOCR 漫画识别'}))
    await act(async () => resolve({models: ['stale'], base_url: 'https://stale.example', endpoint: 'https://stale.example/models', protocol: 'openai'}))
    expect(screen.getByRole('button', {name: '运行设备'})).toHaveTextContent('CPU')
    expect(screen.queryByLabelText('API Key')).not.toBeInTheDocument()
    await user.click(screen.getByRole('button', {name: '保存配置'}))
    expect(api.models.update).toHaveBeenCalledWith('ocr-1', expect.objectContaining({provider: 'manga-ocr', api_key: undefined, config: {device: 'cpu', model: 'local-manga'}}))
  })
})
