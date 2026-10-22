import {act, render as renderView, screen, waitFor, within} from '@testing-library/react'
import type {ReactElement} from 'react'
import userEvent from '@testing-library/user-event'
import {beforeEach, describe, expect, it, vi} from 'vitest'
import {useState} from 'react'
import {api} from '../services/api'
import type {FontPreset, FontResource} from '../types'
import {FontManager} from './FontManager'
import {SettingsHeading} from './SettingsLayout'
import {changeLocale, t} from '../i18n'
import {GlobalDialogProvider} from './GlobalDialog'

const render=(element:ReactElement)=>renderView(element,{wrapper:GlobalDialogProvider})

const presets: FontPreset[] = [
  {id:'lxgw-wenkai',name:'霞鹜文楷',family:'LXGW WenKai',description:'自然的楷书笔触，适合对白、旁白与日常手写。',languages:['zh-Hans','zh-Hant','ja','en'],category:'handwriting',size_bytes:25928940,license:'OFL-1.1',source_url:'https://github.com/lxgw/LxgwWenKai',license_url:'https://example.com/OFL.txt',installed_filename:null},
  {id:'comic-neue',name:'Comic Neue',family:'Comic Neue',description:'清晰自然的英文漫画字体，适合气泡对白。',languages:['en'],category:'handwriting',size_bytes:57248,license:'OFL-1.1',source_url:'https://github.com/crozynski/comicneue',license_url:'https://example.com/OFL.txt',installed_filename:null},
]
const installed: FontResource = {name:'Comic Neue',filename:'ComicNeue-Regular.ttf',path:'fonts/ComicNeue-Regular.ttf',preset_id:'comic-neue'}

function Library({initial = []}: {initial?: FontResource[]}) {
  const [fonts, setFonts] = useState(initial)
  return <FontManager fonts={fonts} onChanged={async () => setFonts(await api.fonts.list())}/>
}

beforeEach(() => {
  vi.restoreAllMocks()
  vi.spyOn(api.fonts,'presets').mockResolvedValue(presets)
  vi.spyOn(api.fonts,'list').mockResolvedValue([installed])
  vi.spyOn(api.fonts,'install').mockResolvedValue(installed)
  vi.spyOn(api.fonts,'remove').mockResolvedValue(undefined)
})

describe('font library', () => {
  it.each(['zh-CN','zh-TW','en','ja'] as const)('omits explanatory copy in %s while retaining font information and empty-search actions',async locale => {
    changeLocale(locale)
    const user=userEvent.setup()
    const {container}=render(<><SettingsHeading id="fonts"/><Library/></>)
    expect(await screen.findByRole('article',{name:'Comic Neue'})).toBeVisible()
    expect(container.querySelector('.settings-page-heading p')).toBeNull()
    expect(screen.queryByText(t('精选开源字体，点击下载后即可用于排版。无需账号或密钥。'))).not.toBeInTheDocument()
    expect(screen.queryByText(t('预设展示真实字形示例；下载后可在“已安装字体”中输入文字预览。字体缺少的字符会使用内置字体补全。'))).not.toBeInTheDocument()
    expect(container.querySelector('.font-library-help')).toBeNull()
    expect(screen.getByRole('article',{name:'Comic Neue'})).toHaveTextContent('OFL-1.1')
    expect(within(screen.getByRole('article',{name:'Comic Neue'})).getByRole('button')).toBeEnabled()
    await user.type(screen.getByRole('searchbox'),'no-matching-font')
    expect(screen.getByText(t('没有找到匹配的字体'))).toBeVisible()
    expect(container.querySelector('.font-library-empty p')).toBeNull()
    await user.click(screen.getByRole('button',{name:t('清除筛选')}))
    expect(screen.getByRole('article',{name:'Comic Neue'})).toBeVisible()
  })
  it('shows real font samples without fetching full fonts and searches Chinese and English aliases', async () => {
    const user = userEvent.setup()
    render(<Library/>)
    expect(await screen.findByRole('article',{name:'霞鹜文楷'})).toBeVisible()
    expect(screen.getByRole('img',{name:'霞鹜文楷字体效果'}).style.maskImage).toContain('lxgw-wenkai')
    expect(api.fonts.install).not.toHaveBeenCalled()
    await user.type(screen.getByRole('searchbox',{name:'搜索字体'}),'LXGW')
    expect(screen.getByRole('article',{name:'霞鹜文楷'})).toBeVisible()
    expect(screen.queryByRole('article',{name:'Comic Neue'})).not.toBeInTheDocument()
    await user.click(screen.getByRole('button',{name:'清空搜索'}))
    await user.type(screen.getByRole('searchbox'),' 气泡 ')
    expect(screen.getByRole('article',{name:'Comic Neue'})).toBeVisible()
    expect(screen.queryByRole('article',{name:'霞鹜文楷'})).not.toBeInTheDocument()
  })
  it('filters languages and restores an empty search', async () => {
    const user = userEvent.setup()
    render(<Library/>)
    await screen.findByRole('article',{name:'Comic Neue'})
    await user.click(screen.getByRole('button',{name:'字体语言'}))
    await user.click(screen.getByRole('option',{name:'繁体中文'}))
    expect(screen.queryByRole('article',{name:'Comic Neue'})).not.toBeInTheDocument()
    await user.type(screen.getByRole('searchbox'),'not-a-font')
    expect(screen.getByText('没有找到匹配的字体')).toBeVisible()
    await user.click(screen.getByRole('button',{name:'清除筛选'}))
    expect(screen.getByRole('searchbox')).toHaveValue('')
    expect(screen.getByRole('article',{name:'Comic Neue'})).toBeVisible()
  })
  it('downloads once, marks installed fonts and includes them in the installed view', async () => {
    const user = userEvent.setup()
    let finish!: (font: FontResource) => void
    vi.mocked(api.fonts.install).mockReturnValue(new Promise(resolve => {finish = resolve}))
    render(<Library/>)
    await user.dblClick(await screen.findByRole('button',{name:'下载Comic Neue'}))
    expect(api.fonts.install).toHaveBeenCalledTimes(1)
    expect(screen.getByText('正在下载并校验字体，请稍候…')).toBeVisible()
    expect(screen.getByRole('button',{name:'下载霞鹜文楷'})).toBeDisabled()
    await act(async () => finish(installed))
    await waitFor(() => expect(screen.getByRole('button',{name:'下载Comic Neue'})).toHaveTextContent('已下载'))
    const dialog=await screen.findByRole('status',{name:'字体管理'})
    expect(dialog).toHaveTextContent('字体“Comic Neue”已下载，可在排版中使用。')
    expect(document.querySelector('.settings-font-manager .settings-notice')).toBeNull()
    await user.click(within(dialog).getByRole('button',{name:'关闭提示'}))
    await user.click(screen.getByRole('button',{name:/已安装字体/}))
    expect(screen.getByText('Comic Neue')).toBeVisible()
    expect(screen.getByLabelText('预览文字')).toBeVisible()
    expect(screen.getByRole('button',{name:'删除Comic Neue'})).toBeEnabled()
  })
  it('retains uploaded fonts when preset loading fails and can retry without reloading the page', async () => {
    const user = userEvent.setup()
    vi.mocked(api.fonts.presets).mockRejectedValueOnce(new Error('网络不可用'))
    render(<Library initial={[installed]}/>)
    expect(await screen.findByRole('alert')).toHaveTextContent('网络不可用')
    await user.click(screen.getByRole('button',{name:/已安装字体/}))
    expect(screen.getByText('Comic Neue')).toBeVisible()
    await user.click(screen.getByRole('button',{name:/字体预设/}))
    await user.click(screen.getByRole('button',{name:'重新加载'}))
    expect(await screen.findByRole('article',{name:'霞鹜文楷'})).toBeVisible()
  })
  it('shows download errors without marking the font installed and allows retry', async () => {
    const user = userEvent.setup()
    vi.mocked(api.fonts.install).mockRejectedValueOnce(new Error('字体文件校验失败，未安装，请重试。'))
    render(<Library/>)
    await user.click(await screen.findByRole('button',{name:'下载Comic Neue'}))
    const dialog=await screen.findByRole('alert',{name:'字体管理'})
    expect(dialog).toHaveTextContent('校验失败')
    expect(document.querySelector('.settings-font-manager .settings-error')).toBeNull()
    const card = screen.getByRole('article',{name:'Comic Neue'})
    expect(within(card).getByRole('button')).toBeEnabled()
    expect(within(card).queryByText('已下载')).not.toBeInTheDocument()
    await user.click(within(dialog).getByRole('button',{name:'关闭提示'}))
    await user.click(within(card).getByRole('button'))
    await waitFor(() => expect(within(card).getByRole('button')).toHaveTextContent('已下载'))
  })
  it('handles later font updates from the settings parent and deletion makes the preset downloadable again', async () => {
    const user = userEvent.setup()
    vi.mocked(api.fonts.list).mockResolvedValue([])
    render(<Library initial={[installed]}/>)
    expect(await screen.findByRole('button',{name:'下载Comic Neue'})).toHaveTextContent('已下载')
    await user.click(screen.getByRole('button',{name:/已安装字体/}))
    await user.click(screen.getByRole('button',{name:'删除Comic Neue'}))
    const dialog=await screen.findByRole('status',{name:'字体管理'})
    expect(dialog).toHaveTextContent('字体“Comic Neue”已删除。')
    await user.click(within(dialog).getByRole('button',{name:'关闭提示'}))
    await waitFor(() => expect(screen.queryByText('Comic Neue')).not.toBeInTheDocument())
    await user.click(screen.getByRole('button',{name:/字体预设/}))
    expect(screen.getByRole('button',{name:'下载Comic Neue'})).toBeEnabled()
  })
  it('keeps custom font uploads available alongside built-in and preset fonts', async () => {
    const user = userEvent.setup()
    const font = {name:'My lettering',filename:'MyLettering.ttf',path:'fonts/MyLettering.ttf'}
    const upload = vi.spyOn(api.fonts,'upload').mockResolvedValue(font)
    vi.mocked(api.fonts.list).mockResolvedValue([font])
    render(<Library/>)
    await screen.findByRole('article',{name:'Comic Neue'})
    const file = new File(['font'],'lettering.ttf',{type:'font/ttf'})
    await user.upload(screen.getByLabelText('上传字体文件'), file)
    await waitFor(() => expect(upload).toHaveBeenCalledWith(file))
    const dialog=await screen.findByRole('status',{name:'字体管理'})
    expect(dialog).toHaveTextContent('字体“My lettering”已添加。')
    await user.click(within(dialog).getByRole('button',{name:'关闭提示'}))
    await user.click(screen.getByRole('button',{name:/已安装字体/}))
    expect(await screen.findByText('My lettering')).toBeVisible()
    expect(screen.getByText('Noto Sans CJK SC')).toBeVisible()
    await user.type(screen.getByRole('searchbox'),'My lettering')
    expect(screen.getByText('My lettering')).toBeVisible()
    expect(screen.queryByText('Noto Sans CJK SC')).not.toBeInTheDocument()
  })
})
