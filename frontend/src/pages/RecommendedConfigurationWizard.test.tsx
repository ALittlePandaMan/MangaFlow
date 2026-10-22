import {render, screen, within, waitFor} from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import {describe, expect, it, vi} from 'vitest'
import type {ModelConfiguration, ModelDescriptor} from '../types'
import {RecommendedConfigurationWizard} from './SettingsPage'

const available = [
  ['detection','rfdetr'],['detection','opencv-fallback'],['inpainting','lama'],
  ['ocr','paddleocr-vl-1.6'],['rendering','pillow'],['translation','openai-compatible'],['translation','passthrough'],['translation','google-web'],['ocr','paddleocr'],['ocr','cloud-ocr'],['ocr','review-fallback'],
].map(([kind,name]) => ({kind,name,description:name,devices:['rfdetr','lama','paddleocr-vl-1.6'].includes(name) ? ['cpu','cuda'] : ['cpu'],supports_batch:false,installed:true,orientations:[],capabilities:{},is_fallback:false})) as ModelDescriptor[]
const translation: ModelConfiguration = {id:'translation',kind:'translation',provider:'openai-compatible',name:'云端翻译',enabled:true,is_default:true,has_api_key:true,config:{base_url:'https://example.com/v1',model:'test-model',api_protocol:'openai'},capabilities:{...available[5]}}

describe('recommended configuration layout',() => {
  it.each(['paddleocr','review-fallback'])('omits retired choices and does not preselect a %s OCR default',async provider=>{
    const user = userEvent.setup()
    const legacy:ModelConfiguration = {...translation,kind:'ocr',provider,config:{device:'cpu'},capabilities:{...available.find(item=>item.name===provider)!}}
    render(<RecommendedConfigurationWizard available={available} configured={[translation,legacy]} fonts={[]} onInstall={vi.fn()}/>)
    const nav = screen.getByRole('navigation',{name:'推荐配置阶段'})
    await user.click(within(nav).getByRole('button',{name:'OCR 识别'}))
    expect(screen.queryByRole('button',{name:/PaddleOCR 文字识别/})).not.toBeInTheDocument()
    expect(screen.queryByRole('button',{name:/云端 OCR（通用接口）/})).not.toBeInTheDocument()
    expect(screen.queryByRole('button',{name:/人工审核占位|review-fallback/})).not.toBeInTheDocument()
    expect(screen.getByRole('button',{name:/PaddleOCR-VL 1.6/,pressed:true})).toBeInTheDocument()
    await user.click(within(nav).getByRole('button',{name:'云端翻译'}))
    expect(screen.queryByRole('button',{name:/不翻译（原文直通）/})).not.toBeInTheDocument()
  })

  it('installs keyless translation without sending an earlier LLM secret draft',async()=>{
    const onInstall = vi.fn().mockResolvedValue(undefined), user=userEvent.setup()
    render(<RecommendedConfigurationWizard available={available} configured={[translation]} fonts={[]} onInstall={onInstall}/>)
    await user.click(within(screen.getByRole('navigation',{name:'推荐配置阶段'})).getByRole('button',{name:'云端翻译'}))
    await user.type(screen.getByLabelText('API 密钥'),'llm-secret-draft')
    await user.click(screen.getByRole('button',{name:/Google 网页翻译（免配置）/}))
    expect(screen.queryByLabelText('API 密钥')).not.toBeInTheDocument()
    expect(screen.queryByRole('button',{name:'首次配置翻译模型'})).not.toBeInTheDocument()
    await user.click(screen.getByRole('button',{name:'安装所选配置'}))
    await waitFor(()=>expect(onInstall).toHaveBeenCalledOnce())
    expect(onInstall.mock.calls[0][0].find((item:{kind:string})=>item.kind==='translation'))
      .toMatchObject({provider:'google-web',api_key:undefined,config:undefined})
  })

  it('keeps the keyless default while retaining the saved LLM connection for switching back',async()=>{
    const user=userEvent.setup()
    const keyless:ModelConfiguration={...translation,id:'google',provider:'google-web',has_api_key:false,config:{timeout:30}}
    render(<RecommendedConfigurationWizard available={available} configured={[{...translation,is_default:false},keyless]} fonts={[]} onInstall={vi.fn()}/>)
    await user.click(within(screen.getByRole('navigation',{name:'推荐配置阶段'})).getByRole('button',{name:'云端翻译'}))
    expect(screen.getByRole('button',{name:/Google 网页翻译（免配置）/,pressed:true})).toBeInTheDocument()
    await user.click(screen.getByRole('button',{name:/自定义云端翻译/}))
    expect(screen.getByRole('button',{name:'首次配置翻译模型'})).toHaveTextContent('test-model')
    expect(screen.getByLabelText('API 密钥')).toHaveAttribute('placeholder','••••••••')
  })
  it('puts the stage navigation above the model list and keeps the footer outside the scroller',async () => {
    const user = userEvent.setup()
    render(<RecommendedConfigurationWizard available={available} configured={[translation]} fonts={[]} onInstall={vi.fn()} onCancel={vi.fn()}/>)
    const nav = screen.getByRole('navigation',{name:'推荐配置阶段'})
    expect(within(nav).getAllByRole('button')).toHaveLength(6)
    expect(nav.parentElement).toHaveClass('recommendation-scroll')
    const install = screen.getByRole('button',{name:'安装所选配置'})
    expect(install.closest('.recommendation-footer')?.parentElement).toHaveClass('settings-recommendations')
    expect(install.closest('.recommendation-scroll')).toBeNull()
    const model = screen.getByRole('button',{name:/RF-DETR 漫画版面检测/,pressed:true})
    expect(model).toHaveClass('recommendation-candidate-select')
    expect(screen.getByRole('button',{name:'RF-DETR 漫画版面检测运行设备'}).closest('.recommendation-candidates')).toBeNull()
    await user.click(within(nav).getByRole('button',{name:'OCR 识别'}))
    expect(screen.getByRole('button',{name:/PaddleOCR-VL 1.6/,pressed:true})).toBeInTheDocument()
    await user.click(within(nav).getByRole('button',{name:'字体资源'}))
    expect(screen.getByRole('heading',{name:'字体包'})).toBeInTheDocument()
  })
  it('preserves choices across stages and sends the same six-stage installation contract',async () => {
    const onInstall = vi.fn().mockResolvedValue(undefined), user = userEvent.setup()
    render(<RecommendedConfigurationWizard available={available} configured={[translation]} fonts={[]} onInstall={onInstall}/>)
    await user.click(screen.getByRole('button',{name:/OpenCV 文本检测/}))
    expect(screen.queryByRole('button',{name:'RF-DETR 漫画版面检测运行设备'})).not.toBeInTheDocument()
    const nav = screen.getByRole('navigation',{name:'推荐配置阶段'})
    await user.click(within(nav).getByRole('button',{name:'云端翻译'}))
    expect(screen.getByRole('button',{name:'首次配置翻译模型'})).toHaveTextContent('test-model')
    const key = screen.getByLabelText('API 密钥')
    expect(key).toHaveValue('')
    expect(key).toHaveAttribute('placeholder','••••••••')
    await user.click(key)
    expect(key).toHaveAttribute('placeholder','')
    await user.click(within(nav).getByRole('button',{name:'文字检测'}))
    expect(screen.getByRole('button',{name:/OpenCV 文本检测/,pressed:true})).toBeInTheDocument()
    await user.click(screen.getByRole('button',{name:'安装所选配置'}))
    await waitFor(() => expect(onInstall).toHaveBeenCalledOnce())
    const [selections,preload] = onInstall.mock.calls[0]
    expect(preload).toBe(true)
    expect(selections).toHaveLength(6)
    expect(selections).toEqual(expect.arrayContaining([
      expect.objectContaining({kind:'detection',provider:'opencv-fallback',device:'cpu'}),
      expect.objectContaining({kind:'translation',provider:'openai-compatible',config:expect.objectContaining({model:'test-model',base_url:'https://example.com/v1'})}),
    ]))
    expect(selections.find((item:{kind:string}) => item.kind === 'translation').api_key).toBeUndefined()
  })
  it('keeps setup safeguards and reports installation failures inline',async () => {
    const user = userEvent.setup()
    const {unmount} = render(<RecommendedConfigurationWizard available={available} configured={[translation]} fonts={[]} required onInstall={vi.fn()}/>)
    expect(screen.getByRole('switch',{name:'立即下载并加载本地模型'})).toBeDisabled()
    expect(screen.getByRole('button',{name:'安装、检测并进入工作台'})).toBeDisabled()
    expect(screen.queryByRole('button',{name:'取消'})).not.toBeInTheDocument()
    unmount()
    render(<RecommendedConfigurationWizard available={available} configured={[translation]} fonts={[]} onInstall={vi.fn().mockRejectedValue(new Error('安装失败，请重试'))}/>)
    await user.click(screen.getByRole('button',{name:'安装所选配置'}))
    expect(await screen.findByRole('alert')).toHaveTextContent('安装失败，请重试')
    expect(screen.getByRole('button',{name:'安装所选配置'})).toBeEnabled()
  })
})
