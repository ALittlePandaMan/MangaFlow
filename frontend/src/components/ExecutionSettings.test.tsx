import {render, screen, waitFor} from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import {describe,it,expect,vi} from 'vitest'
import {ExecutionSettings} from './ExecutionSettings'
import {api} from '../services/api'
import type {ExecutionStatus} from '../types'

const state: ExecutionStatus = {
  policy:{mode:'auto',max_pages:4,cpu_workers:4,memory_reserve_mb:1024},
  pages:{active:2,limit:2,maximum:4,peak:2,reason:'保守起步'},
  cpu:{active:1,limit:2,maximum:4,peak:2,reason:'保守起步'},
  models:{active:1,limit:1},native_threads:2,recent_stages:[],
  resources:{known:true,available_bytes:8*1024**3,total_bytes:16*1024**3,cpu_percent:20,cpu_cores:8,rss_bytes:1024**3},
}

describe('execution policy settings',() => {
  it('shows GPU memory, lock waiting and stage timings without changing the model limit',async () => {
    vi.spyOn(api.execution,'get').mockResolvedValue({...state,
      models:{active:1,limit:1,calls:3,wait_seconds:2.5,run_seconds:4},
      gpu:{known:true,devices:[{index:0,name:'V100',total_bytes:16*1024**3,used_bytes:10*1024**3,free_bytes:6*1024**3,utilization_percent:40}]},
      stage_timings:[{stage:'ocr',samples:3,mean_seconds:2,p95_seconds:3}],
    })
    render(<ExecutionSettings onCancel={() => {}}/>)
    expect(await screen.findByText(/V100 · 显存 10.0 \/ 16.0 GB/)).toBeInTheDocument()
    expect(screen.getByText(/等待 2.50 s/)).toBeInTheDocument()
    expect(screen.getByText(/平均 2.00 s · P95 3.00 s/)).toBeInTheDocument()
    expect(screen.getByText('本地模型：1 / 1 路')).toBeInTheDocument()
  })
  it('loads on demand, persists a cap and does not reset edits when refreshing status',async () => {
    const get = vi.spyOn(api.execution,'get').mockResolvedValue(state)
    const save = vi.spyOn(api.execution,'update').mockImplementation(async policy => ({...state,policy}))
    render(<ExecutionSettings onCancel={() => {}}/>)
    const cap = await screen.findByLabelText('页面并发上限')
    await userEvent.clear(cap); await userEvent.type(cap,'3')
    await userEvent.click(screen.getByRole('button',{name:'刷新状态'}))
    await waitFor(() => expect(get).toHaveBeenCalledTimes(2))
    expect(cap).toHaveValue(3)
    await userEvent.click(screen.getByRole('button',{name:'保存性能设置'}))
    await waitFor(() => expect(save).toHaveBeenCalledWith({...state.policy,max_pages:3}))
    expect(await screen.findByText(/设置已生效/)).toBeInTheDocument()
  })
  it('shows actual backends and safe fallback reasons without starting another request',async () => {
    const get = vi.spyOn(api.execution,'get').mockResolvedValue({...state,inference:[
      {kind:'segmentation',name:'comic-text-detector',backend:'opencv',device:'cpu',state:'ready',fallback_reason:'cuda_memory_reserve'},
      {kind:'ocr',name:'manga-ocr',backend:'torch',device:'cuda:0',state:'ready',fallback_reason:null},
      {kind:'detection',name:'rfdetr',backend:'torch-worker',device:null,state:'unloaded',fallback_reason:null},
    ]})
    render(<ExecutionSettings onCancel={() => {}}/>)
    expect(await screen.findByText(/comic-text-detector · opencv · cpu · 回退原因：显存余量不足/)).toBeInTheDocument()
    expect(screen.getByText('manga-ocr · torch · cuda:0')).toBeInTheDocument()
    expect(screen.getByText('rfdetr · torch-worker · 未加载')).toBeInTheDocument()
    expect(get).toHaveBeenCalledTimes(1)
  })
  it('disables parallel controls in serial mode',async () => {
    vi.spyOn(api.execution,'get').mockResolvedValue({...state,policy:{...state.policy,mode:'serial'}})
    render(<ExecutionSettings onCancel={() => {}}/>)
    expect(await screen.findByLabelText('页面并发上限')).toBeDisabled()
    expect(screen.getByLabelText('CPU 计算线程上限')).toBeDisabled()
    expect(screen.getByLabelText('GPU 并行上限')).toBeDisabled()
    expect(screen.getByLabelText('保留可用内存（MB）')).toBeEnabled()
    expect(screen.getByLabelText('显存预留（MB）')).toHaveValue(2048)
  })
})
