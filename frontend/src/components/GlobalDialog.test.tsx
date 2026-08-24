import {render, screen, waitFor} from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import {useState} from 'react'
import {describe, expect, it} from 'vitest'

import {GlobalDialogProvider, useGlobalDialog} from './GlobalDialog'

function DialogHarness() {
  const dialog = useGlobalDialog()
  const [result, setResult] = useState('等待操作')
  const openConfirmation = async () => {
    const accepted = await dialog.confirm({title:'删除区域？', message:'此操作无法撤销。', confirmLabel:'删除区域'})
    setResult(accepted ? '已确认' : '已取消')
  }
  return <>
    <button onClick={() => void openConfirmation()}>打开确认</button>
    <output>{result}</output>
  </>
}

describe('GlobalDialogProvider', () => {
  it('resolves a confirmation after the user accepts it', async () => {
    const user = userEvent.setup()
    render(<GlobalDialogProvider><DialogHarness/></GlobalDialogProvider>)

    await user.click(screen.getByRole('button', {name:'打开确认'}))
    expect(screen.getByRole('dialog', {name:'删除区域？'})).toBeInTheDocument()
    expect(screen.getByText('此操作无法撤销。')).toBeInTheDocument()

    await user.click(screen.getByRole('button', {name:'删除区域'}))
    await waitFor(() => expect(screen.getByText('已确认')).toBeInTheDocument())
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  })

  it('resolves a confirmation as cancelled when Escape is pressed', async () => {
    const user = userEvent.setup()
    render(<GlobalDialogProvider><DialogHarness/></GlobalDialogProvider>)

    await user.click(screen.getByRole('button', {name:'打开确认'}))
    await user.keyboard('{Escape}')

    await waitFor(() => expect(screen.getByText('已取消')).toBeInTheDocument())
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  })
})
