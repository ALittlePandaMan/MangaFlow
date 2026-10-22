import {fireEvent, render, screen, waitFor, within} from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import {MemoryRouter, Route, Routes} from 'react-router-dom'
import {afterEach, describe, expect, it, vi} from 'vitest'
import {GlobalDialogProvider} from '../components/GlobalDialog'
import {api} from '../services/api'
import type {Project} from '../types'
import {ProjectsPage} from './ProjectsPage'

const projects: Project[] = ['短标题','一个需要换行展示的较长项目标题','VeryLongUnbrokenProjectName'.repeat(8)].map((name,index) => ({
  id:String(index), name, description:index ? '项目说明文字'.repeat(30) : '',
  source_language:'ja', target_language:'zh-CN', translation_context:{}, settings:{},
  cover_url:null, page_count:10, created_at:'2026-10-21', updated_at:'2026-10-21',
}))

afterEach(() => vi.restoreAllMocks())

function open(items = projects) {
  vi.spyOn(api.projects,'list').mockResolvedValue(items)
  return render(<MemoryRouter initialEntries={['/projects']}><GlobalDialogProvider><Routes>
    <Route path="/projects" element={<ProjectsPage/>}/>
    <Route path="/projects/:projectId/editor" element={<p>工作台路由</p>}/>
  </Routes></GlobalDialogProvider></MemoryRouter>)
}

describe('project card alignment',() => {
  it('reserves two title lines and anchors actions at the bottom for mixed title lengths',async () => {
    open()
    await screen.findByRole('heading',{name:projects[0].name})
    const cards = screen.getAllByRole('article')
    expect(cards).toHaveLength(projects.length)
    for (const [index,card] of cards.entries()) {
      expect(card).toHaveClass('flex','flex-col','min-w-0')
      expect(card.firstElementChild).toHaveClass('h-[150px]','shrink-0')
      const title = within(card).getByRole('heading',{level:2,name:projects[index].name})
      expect(title).toHaveClass('min-h-[3em]','leading-normal','line-clamp-2','[overflow-wrap:anywhere]','text-[21px]')
      expect(title).toHaveAttribute('title',projects[index].name)
      expect(title.parentElement).toHaveClass('flex','flex-1','flex-col','min-w-0')
      expect(within(card).getByRole('button',{name:'打开工作台'}).parentElement).toHaveClass('mt-auto','pt-5')
      expect(within(card).getByRole('button',{name:`编辑项目 ${projects[index].name}`})).toHaveClass('icon-button')
      expect(within(card).getByRole('button',{name:`删除项目 ${projects[index].name}`})).toHaveClass('icon-button','icon-button-danger')
    }
  })

  it('keeps project navigation and editing available after the layout change',async () => {
    open()
    await screen.findByRole('heading',{name:projects[0].name})
    const card = screen.getAllByRole('article')[0]
    fireEvent.click(within(card).getByRole('button',{name:`编辑项目 ${projects[0].name}`}))
    const dialog = screen.getByRole('dialog',{name:'编辑项目'})
    expect(within(dialog).getByRole('textbox',{name:'项目标题'})).toHaveValue(projects[0].name)
    fireEvent.click(within(dialog).getByRole('button',{name:'取消'}))
    fireEvent.click(within(card).getByRole('button',{name:'打开工作台'}))
    expect(screen.getByText('工作台路由')).toBeInTheDocument()
  })
})

describe('project details',() => {
  it('shows deletion failures through the global toast without adding a page error box',async () => {
    vi.spyOn(api.projects,'remove').mockRejectedValue(new Error('暂时无法删除'))
    open()
    const card=(await screen.findAllByRole('article'))[0]
    fireEvent.click(within(card).getByRole('button',{name:`删除项目 ${projects[0].name}`}))
    fireEvent.click(screen.getByRole('button',{name:'删除项目'}))
    const toast=await screen.findByRole('alert',{name:'删除项目'})
    expect(toast).toHaveClass('global-alert')
    expect(toast).toHaveTextContent('暂时无法删除')
    expect(screen.getAllByRole('alert')).toHaveLength(1)
    expect(card).toBeInTheDocument()
  })
  it('omits descriptions and displays the saved language direction on cards',async () => {
    open([{...projects[0],description:'不需要展示的项目说明',source_language:'en',target_language:'ja'}])
    const card = (await screen.findAllByRole('article'))[0]
    expect(within(card).getByText('英语')).toBeInTheDocument()
    expect(within(card).getByText('日语')).toBeInTheDocument()
    expect(screen.queryByText('不需要展示的项目说明')).not.toBeInTheDocument()
    expect(screen.queryByText('尚未填写项目说明')).not.toBeInTheDocument()
  })

  it('creates a project with only its title and the default Japanese-to-Chinese direction',async () => {
    const create = vi.spyOn(api.projects,'create').mockResolvedValue({...projects[0],name:'新标题'})
    open()
    await screen.findByRole('heading',{name:projects[0].name})
    fireEvent.click(screen.getByRole('button',{name:'新建项目'}))
    const dialog = screen.getByRole('dialog',{name:'新建项目'})
    expect(within(dialog).getAllByRole('textbox')).toHaveLength(1)
    expect(within(dialog).getByRole('button',{name:'原文语言'})).toHaveTextContent('日语')
    expect(within(dialog).getByRole('button',{name:'译文语言'})).toHaveTextContent('简体中文')
    expect(within(dialog).queryByText(/项目创建后|创建漫画翻译工作区|项目说明/)).not.toBeInTheDocument()
    expect(dialog).not.toHaveAttribute('aria-describedby')
    const submit = within(dialog).getByRole('button',{name:'创建项目'})
    expect(submit).toBeDisabled()
    fireEvent.change(within(dialog).getByRole('textbox',{name:'项目标题'}),{target:{value:'  '}})
    expect(submit).toBeDisabled()
    fireEvent.change(within(dialog).getByRole('textbox',{name:'项目标题'}),{target:{value:' 新标题 '}})
    fireEvent.click(submit)
    await waitFor(() => expect(create).toHaveBeenCalledExactlyOnceWith({name:'新标题',source_language:'ja',target_language:'zh-CN'}))
    expect(await screen.findByText('工作台路由')).toBeInTheDocument()
  })

  it('sends both chosen languages rather than the previous hard-coded direction',async () => {
    const user = userEvent.setup()
    const create = vi.spyOn(api.projects,'create').mockResolvedValue({...projects[0],source_language:'en',target_language:'fr'})
    open()
    await screen.findByRole('heading',{name:projects[0].name})
    await user.click(screen.getByRole('button',{name:'新建项目'}))
    const dialog = screen.getByRole('dialog',{name:'新建项目'})
    await user.type(within(dialog).getByRole('textbox',{name:'项目标题'}),'方向测试')
    await user.click(within(dialog).getByRole('button',{name:'原文语言'}))
    await user.click(screen.getByRole('option',{name:'英语'}))
    await user.click(within(dialog).getByRole('button',{name:'译文语言'}))
    await user.click(screen.getByRole('option',{name:'法语'}))
    expect(within(dialog).getByRole('textbox',{name:'项目标题'})).toHaveValue('方向测试')
    await user.click(within(dialog).getByRole('button',{name:'创建项目'}))
    await waitFor(() => expect(create).toHaveBeenCalledExactlyOnceWith({name:'方向测试',source_language:'en',target_language:'fr'}))
    expect(await screen.findByText('工作台路由')).toBeInTheDocument()
  })

  it('keeps the form and its draft when creating fails',async () => {
    vi.spyOn(api.projects,'create').mockRejectedValue(new Error('暂时无法创建'))
    open()
    await screen.findByRole('heading',{name:projects[0].name})
    fireEvent.click(screen.getByRole('button',{name:'新建项目'}))
    const dialog = screen.getByRole('dialog',{name:'新建项目'})
    fireEvent.change(within(dialog).getByRole('textbox',{name:'项目标题'}),{target:{value:'保留标题'}})
    fireEvent.click(within(dialog).getByRole('button',{name:'创建项目'}))
    expect(await within(dialog).findByRole('alert')).toHaveTextContent('暂时无法创建')
    expect(within(dialog).getByRole('textbox',{name:'项目标题'})).toHaveValue('保留标题')
    expect(within(dialog).getByRole('button',{name:'创建项目'})).toBeEnabled()
    expect(within(dialog).getByRole('button',{name:'原文语言'})).toHaveTextContent('日语')
  })

  it('edits the title and direction without clearing hidden descriptions or legacy language codes',async () => {
    const project = {...projects[0],description:'原有说明不修改',source_language:'custom-language',target_language:'zh'}
    const update = vi.spyOn(api.projects,'update').mockResolvedValue({...project,name:'修改后的标题'})
    open([project])
    await screen.findByRole('heading',{name:project.name})
    fireEvent.click(screen.getByRole('button',{name:`编辑项目 ${project.name}`}))
    const dialog = screen.getByRole('dialog',{name:'编辑项目'})
    expect(within(dialog).getAllByRole('textbox')).toHaveLength(1)
    expect(within(dialog).getByRole('button',{name:'原文语言'})).toHaveTextContent('custom-language')
    expect(within(dialog).getByRole('button',{name:'译文语言'})).toHaveTextContent('简体中文')
    expect(within(dialog).queryByRole('textbox',{name:'项目说明'})).not.toBeInTheDocument()
    fireEvent.change(within(dialog).getByRole('textbox',{name:'项目标题'}),{target:{value:' 修改后的标题 '}})
    fireEvent.click(within(dialog).getByRole('button',{name:'保存修改'}))
    await waitFor(() => expect(update).toHaveBeenCalledExactlyOnceWith(project.id,{name:'修改后的标题',source_language:'custom-language',target_language:'zh'}))
    expect(await screen.findByRole('heading',{name:'修改后的标题'})).toBeInTheDocument()
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  })
})
