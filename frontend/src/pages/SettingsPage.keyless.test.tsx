import {render, screen, waitFor} from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import {afterEach, describe, expect, it, vi} from 'vitest'
import {GlobalDialogProvider} from '../components/GlobalDialog'
import {api} from '../services/api'
import type {ModelConfiguration, ModelDescriptor} from '../types'
import {SettingsPage} from './SettingsPage'

vi.mock('../components/LoadingUI',async original => ({...await original<typeof import('../components/LoadingUI')>(),useMinimumLoadingTime:(value:boolean) => value}))
const available = ['openai-compatible','google-web','passthrough'].map(name=>({kind:'translation',name,description:name,
  devices:['remote'],supports_batch:true,installed:true,orientations:[],capabilities:{keyless:name==='google-web'},is_fallback:name==='passthrough'})) as ModelDescriptor[]
const llm: ModelConfiguration = {id:'llm',kind:'translation',provider:'openai-compatible',name:'保存的大模型翻译',
  enabled:true,is_default:false,has_api_key:true,config:{base_url:'https://example.com/v1',model:'saved-model'},capabilities:{...available[0]}}
const google: ModelConfiguration = {id:'google',kind:'translation',provider:'google-web',name:'Google 网页翻译（免配置）',
  enabled:true,is_default:true,has_api_key:false,config:{timeout:30},capabilities:{...available[1]}}
afterEach(()=>vi.restoreAllMocks())

async function open(configured:ModelConfiguration[]) {
  vi.spyOn(api.models,'list').mockResolvedValue({available,configured})
  vi.spyOn(api.fonts,'list').mockResolvedValue([])
  vi.spyOn(api.models,'update').mockResolvedValue(google)
  vi.spyOn(api.models,'create').mockResolvedValue(google)
  vi.spyOn(api.models,'discover').mockResolvedValue({models:[],base_url:'https://example.com/v1',endpoint:'',protocol:'openai'})
  const user = userEvent.setup()
  render(<GlobalDialogProvider><SettingsPage/></GlobalDialogProvider>)
  await waitFor(()=>expect(screen.getByRole('button',{name:'编辑云端翻译配置'})).toBeEnabled())
  await user.click(screen.getByRole('button',{name:'编辑云端翻译配置'}))
  return user
}

describe('keyless translation settings',()=>{
  it('removes passthrough and manual model entry and defaults to 2048 output tokens',async()=>{
    const user = await open([{...llm,is_default:true}])
    expect(screen.queryByRole('button',{name:'手动输入模型'})).not.toBeInTheDocument()
    await user.click(screen.getByText('高级选项'))
    expect(screen.getByLabelText('最大输出 Token 数')).toHaveValue('2048')
    await user.click(screen.getByRole('button',{name:'保存配置'}))
    await waitFor(()=>expect(api.models.update).toHaveBeenCalledWith('llm',expect.objectContaining({config:expect.objectContaining({max_tokens:2048})})))
    await user.click(screen.getByRole('button',{name:'当前模型'}))
    expect(screen.queryByRole('option',{name:'不翻译（原文直通）'})).not.toBeInTheDocument()
    expect(screen.getByRole('option',{name:'Google 网页翻译（免配置）'})).toBeVisible()
  })

  it('restores an active keyless provider even when a saved LLM configuration exists',async()=>{
    const user = await open([llm,google])
    expect(screen.getByRole('button',{name:'当前模型'})).toHaveTextContent('Google 网页翻译（免配置）')
    expect(screen.queryByLabelText('API Key')).not.toBeInTheDocument()
    expect(screen.queryByLabelText('API Base URL')).not.toBeInTheDocument()
    expect(screen.queryByRole('button',{name:'选择翻译模型'})).not.toBeInTheDocument()
    expect(screen.queryByText('高级选项')).not.toBeInTheDocument()
    await user.click(screen.getByRole('button',{name:'保存配置'}))
    await waitFor(()=>expect(api.models.update).toHaveBeenCalledWith('google',expect.objectContaining({
      provider:'google-web',api_key:undefined,config:{timeout:30},is_default:true})))
    expect(api.models.discover).not.toHaveBeenCalled()
  })

  it('creates a keyless selection without copying an LLM key, URL, model or prompt',async()=>{
    const user = await open([{...llm,is_default:true}])
    await user.type(screen.getByLabelText('API Key'),'replacement-test-key')
    await user.click(screen.getByRole('button',{name:'当前模型'}))
    await user.click(screen.getByRole('option',{name:'Google 网页翻译（免配置）'}))
    await user.click(screen.getByRole('button',{name:'保存配置'}))
    await waitFor(()=>expect(api.models.create).toHaveBeenCalledOnce())
    const payload = vi.mocked(api.models.create).mock.calls[0][0]
    expect(payload).toMatchObject({provider:'google-web',api_key:undefined,config:{timeout:30}})
    expect(api.models.update).not.toHaveBeenCalled()
  })

  it('restores the saved LLM connection when switching back instead of replacing it',async()=>{
    const user = await open([llm,google])
    await user.click(screen.getByRole('button',{name:'当前模型'}))
    await user.click(screen.getByRole('option',{name:'自定义云端翻译'}))
    expect(screen.getByLabelText('API Base URL')).toHaveValue('https://example.com/v1')
    expect(screen.getByLabelText('API Key')).toHaveAttribute('placeholder','••••••••')
    expect(screen.getByRole('button',{name:'选择翻译模型'})).toHaveTextContent('saved-model')
    expect(api.models.update).not.toHaveBeenCalled()
    expect(api.models.create).not.toHaveBeenCalled()
  })
})
