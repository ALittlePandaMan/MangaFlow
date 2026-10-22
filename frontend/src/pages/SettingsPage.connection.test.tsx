import {act, fireEvent, render, screen, waitFor, within} from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import {beforeEach, describe, expect, it, vi} from 'vitest'
import {GlobalDialogProvider} from '../components/GlobalDialog'
import {api} from '../services/api'
import type {ModelConfiguration, ModelDescriptor} from '../types'
import {SettingsPage} from './SettingsPage'

vi.mock('../components/LoadingUI', async original=>({...await original<typeof import('../components/LoadingUI')>(),useMinimumLoadingTime:(value:boolean)=>value}))

const stages=[['detection','文字检测','opencv-fallback'],['ocr','OCR 识别','manga-ocr'],['translation','云端翻译','openai-compatible'],
  ['inpainting','图像修复','opencv'],['rendering','排版渲染','pillow']] as const
const available=stages.map(([kind,,name])=>({kind,name,description:name,devices:kind==='translation' ? ['remote'] : ['cpu','cuda'],
  installed:true,orientations:[],supports_batch:false,capabilities:{},is_fallback:false} as ModelDescriptor))
let configured:ModelConfiguration[]

beforeEach(()=>{
  vi.restoreAllMocks()
  configured=stages.map(([kind,,provider],index)=>({id:kind,kind,provider,name:provider,enabled:true,is_default:true,has_api_key:kind==='translation',capabilities:{...available[index]},
    config:kind==='translation' ? {base_url:'https://example.test/v1',model:'saved-model',api_protocol:'responses'} : kind==='rendering' ? {min_font_size:10} : {device:'cpu'}}))
  vi.spyOn(api.models,'list').mockImplementation(async()=>({available,configured}))
  vi.spyOn(api.fonts,'list').mockResolvedValue([])
  vi.spyOn(api.models,'testConnection').mockImplementation(async payload=>({ok:true,mode:payload.kind==='translation' ? 'remote' : 'local',elapsed_ms:10}))
  vi.spyOn(api.models,'create').mockResolvedValue(configured[0])
  vi.spyOn(api.models,'update').mockResolvedValue(configured[0])
})

async function open(label:string) {
  const user=userEvent.setup()
  render(<GlobalDialogProvider><SettingsPage/></GlobalDialogProvider>)
  await waitFor(()=>expect(screen.getByRole('button',{name:`编辑${label}配置`})).toBeEnabled())
  await user.click(screen.getByRole('button',{name:`编辑${label}配置`}))
  return user
}

describe('service connection tests',()=>{
  it.each(stages)('tests %s using current settings without saving',async(kind,label,provider)=>{
    const user=await open(label)
    await user.click(screen.getByRole('button',{name:'测试连接'}))
    await waitFor(()=>expect(api.models.testConnection).toHaveBeenCalledOnce())
    expect(api.models.testConnection).toHaveBeenCalledWith(expect.objectContaining({kind,provider,config_id:kind,config:expect.objectContaining(configured.find(item=>item.kind===kind)!.config)}))
    expect(await screen.findByText(kind==='translation' ? '连接测试通过' : '本地服务检测通过')).toBeVisible()
    const dialog=screen.getByRole('status',{name:'测试连接'})
    expect(dialog).toHaveTextContent(kind==='translation' ? '连接测试通过' : '本地服务检测通过')
    expect(document.querySelector('.settings-form .settings-notice')).toBeNull()
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    await user.click(within(dialog).getByRole('button',{name:'关闭提示'}))
    expect(screen.queryByText(kind==='translation' ? '连接测试通过' : '本地服务检测通过')).not.toBeInTheDocument()
    expect(api.models.create).not.toHaveBeenCalled()
    expect(api.models.update).not.toHaveBeenCalled()
  })

  it('tests unsaved rendering parameters rather than the stored values',async()=>{
    const user=await open('排版渲染')
    fireEvent.change(screen.getByLabelText('最小字号'),{target:{value:'22'}})
    await user.click(screen.getByRole('button',{name:'测试连接'}))
    expect(api.models.testConnection).toHaveBeenCalledWith(expect.objectContaining({config:{min_font_size:22}}))
    expect(screen.getByLabelText('最小字号')).toHaveValue('22')
    expect(api.models.update).not.toHaveBeenCalled()
  })

  it('blocks duplicate tests and saving until the probe returns, then shows its failure',async()=>{
    let finish!:(result:{ok:boolean,mode:'local',message:string,elapsed_ms:number})=>void
    vi.mocked(api.models.testConnection).mockReturnValue(new Promise(resolve=>{finish=resolve}))
    const user=await open('文字检测')
    const button=screen.getByRole('button',{name:'测试连接'})
    fireEvent.click(button);fireEvent.click(button)
    await waitFor(()=>expect(api.models.testConnection).toHaveBeenCalledOnce())
    expect(screen.getByRole('button',{name:'正在测试…'})).toBeDisabled()
    expect(screen.getByRole('button',{name:'保存配置'})).toBeDisabled()
    await act(async()=>finish({ok:false,mode:'local',message:'Model weights missing',elapsed_ms:1}))
    const dialog=await screen.findByRole('alert',{name:'测试连接'})
    expect(dialog).toHaveTextContent('Model weights missing')
    expect(document.querySelector('.settings-form .settings-error')).toBeNull()
    await user.click(within(dialog).getByRole('button',{name:'关闭提示'}))
    expect(screen.getByRole('button',{name:'测试连接'})).toBeEnabled()
    expect(screen.getByRole('button',{name:'保存配置'})).toBeEnabled()
    await user.click(screen.getByRole('button',{name:'运行设备'}))
    await user.click(screen.getByRole('option',{name:'自动（GPU 优先）'}))
    expect(screen.queryByText('Model weights missing')).not.toBeInTheDocument()
  })

  it('does not show a successful result for credentials edited while testing',async()=>{
    let finish!:(result:{ok:boolean,mode:'remote',elapsed_ms:number})=>void
    vi.mocked(api.models.testConnection).mockReturnValue(new Promise(resolve=>{finish=resolve}))
    const user=await open('云端翻译')
    await user.click(screen.getByRole('button',{name:'测试连接'}))
    expect(api.models.testConnection).toHaveBeenCalledWith(expect.objectContaining({config_id:'translation',api_key:undefined}))
    fireEvent.change(screen.getByLabelText('API Key'),{target:{value:'new-key'}})
    await act(async()=>finish({ok:true,mode:'remote',elapsed_ms:1}))
    expect(screen.queryByText('连接测试通过')).not.toBeInTheDocument()
    expect(screen.getByRole('button',{name:'测试连接'})).toBeEnabled()
  })

  it('reports request failures and allows retry without saving the form',async()=>{
    vi.mocked(api.models.testConnection).mockRejectedValueOnce(new Error('HTTP 503'))
    const user=await open('图像修复')
    await user.click(screen.getByRole('button',{name:'测试连接'}))
    const dialog=await screen.findByRole('alert',{name:'测试连接'})
    expect(dialog).toHaveTextContent('HTTP 503')
    await user.click(within(dialog).getByRole('button',{name:'关闭提示'}))
    await user.click(screen.getByRole('button',{name:'测试连接'}))
    expect(await screen.findByText('本地服务检测通过')).toBeVisible()
    expect(api.models.update).not.toHaveBeenCalled()
  })

  it('requires a model before attempting a cloud connection',async()=>{
    configured=configured.map(item=>item.kind==='translation' ? {...item,config:{...item.config,model:''}} : item)
    const user=await open('云端翻译')
    await user.click(screen.getByRole('button',{name:'测试连接'}))
    expect(await screen.findByRole('alert',{name:'测试连接'})).toHaveTextContent('请先获取并选择模型。')
    expect(api.models.testConnection).not.toHaveBeenCalled()
  })

  it('does not open a stale connection alert after switching settings sections',async()=>{
    let finish!:(result:{ok:boolean,mode:'local',elapsed_ms:number})=>void
    vi.mocked(api.models.testConnection).mockReturnValue(new Promise(resolve=>{finish=resolve}))
    const user=await open('文字检测')
    await user.click(screen.getByRole('button',{name:'测试连接'}))
    await user.click(screen.getByRole('button',{name:'编辑图像修复配置'}))
    await act(async()=>finish({ok:true,mode:'local',elapsed_ms:1}))
    expect(screen.queryByRole('status',{name:'测试连接'})).not.toBeInTheDocument()
    expect(screen.getByRole('heading',{level:1,name:'图像修复'})).toBeVisible()
  })
})
