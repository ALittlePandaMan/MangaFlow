import {act, fireEvent, render, screen} from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import {beforeEach, describe, expect, it, vi} from 'vitest'
import {api} from '../services/api'
import {DEFAULT_TEXT_STYLE, setTextStyleDefaults} from '../features/settings/textStyle'
import {draftRegion} from '../features/editor/lib/regionEditing'
import {TypographySettings} from './TypographySettings'

beforeEach(() => {vi.restoreAllMocks(); setTextStyleDefaults(DEFAULT_TEXT_STYLE)})

describe('default text style settings', () => {
  it('uses the shared form skeleton while loading preferences',async () => {
    let finish!: (value: {shortcuts: Record<string,string>}) => void
    vi.spyOn(api.preferences,'get').mockReturnValue(new Promise(resolve => {finish=resolve}))
    const {unmount} = render(<TypographySettings fonts={[]}/>)
    expect(screen.getByRole('status',{name:'正在读取文本样式…'})).toHaveClass('settings-form-skeleton')
    expect(screen.queryByRole('button',{name:'保存默认样式'})).not.toBeInTheDocument()
    unmount()
    await act(async () => {finish({shortcuts:{}})})
  })
  it('does not allow saving fallback defaults when the initial read fails',async () => {
    vi.spyOn(api.preferences,'get').mockRejectedValue(new Error('读取失败'))
    const save = vi.spyOn(api.preferences,'updateTextStyle')
    render(<TypographySettings fonts={[]}/>)
    expect(await screen.findByRole('alert')).toHaveTextContent('读取失败')
    expect(screen.queryByRole('button',{name:'保存默认样式'})).not.toBeInTheDocument()
    expect(save).not.toHaveBeenCalled()
  })
  it('persists defaults and applies them only to newly created boxes',async () => {
    vi.spyOn(api.preferences,'get').mockResolvedValue({shortcuts:{},text_style:{...DEFAULT_TEXT_STYLE,font_size:36}})
    const save = vi.spyOn(api.preferences,'updateTextStyle').mockImplementation(async text_style => ({shortcuts:{},text_style}))
    const before = draftRegion('page',[[0,0],[30,0],[30,90]],[0,0,30,90],[])
    const user = userEvent.setup()
    render(<TypographySettings fonts={[]}/>)
    const size = await screen.findByLabelText('默认固定字号')
    expect(size).toHaveValue('36')
    await user.click(screen.getByRole('checkbox',{name:'按文本框拟合'}))
    fireEvent.change(size,{target:{value:'42'}})
    await user.click(screen.getByRole('button',{name:'默认文字方向'}))
    await user.click(screen.getByRole('option',{name:'横排'}))
    await user.click(screen.getByRole('button',{name:'保存默认样式'}))
    expect(await screen.findByRole('status')).toHaveTextContent('默认样式已保存')
    expect(save).toHaveBeenCalledWith(expect.objectContaining({font_size:42,auto_font_size:false,orientation:'horizontal'}))
    const after = draftRegion('page',[[0,0],[30,0],[30,90]],[0,0,30,90],[])
    expect(after).toMatchObject({font_size:42,auto_font_size:false,orientation:'horizontal'})
    expect(before).toMatchObject({font_size:28,orientation:'vertical'})
  })
  it('does not change saved defaults when merely restoring the form',async () => {
    setTextStyleDefaults({...DEFAULT_TEXT_STYLE,font_size:44})
    vi.spyOn(api.preferences,'get').mockResolvedValue({shortcuts:{},text_style:{...DEFAULT_TEXT_STYLE,font_size:44}})
    const user = userEvent.setup()
    render(<TypographySettings fonts={[]}/>)
    await screen.findByLabelText('默认固定字号')
    await user.click(screen.getByRole('button',{name:'恢复默认'}))
    expect(screen.getByLabelText('默认固定字号')).toHaveValue('28')
    expect(draftRegion('page',[],[0,0,50,50],[]).font_size).toBe(44)
  })
})
