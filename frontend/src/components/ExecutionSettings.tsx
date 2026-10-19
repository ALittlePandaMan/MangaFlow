import {useEffect, useState} from 'react'
import {api} from '../services/api'
import type {ExecutionPolicy, ExecutionStatus} from '../types'
import {buttonClass, inputClass, primaryButtonClass} from '../ui'
import {SelectControl} from './FormControls'

const fallbackLabels: Record<string, string> = {
  cuda_provider_unavailable: 'CUDA 推理后端不可用',
  cuda_memory_reserve: '显存余量不足',
  cuda_provider_initialization_failed: 'CUDA 推理后端初始化失败',
  cuda_initialization_failed: 'CUDA 模型初始化失败',
  cuda_inference_failed: 'CUDA 推理失败',
  accelerator_unavailable: '加速设备不可用',
  accelerator_initialization_failed: '加速模型初始化失败',
  accelerator_inference_failed: '加速推理失败',
}

export function ExecutionSettings({onCancel}: {onCancel: () => void}) {
  const [status, setStatus] = useState<ExecutionStatus | null>(null)
  const [policy, setPolicy] = useState<ExecutionPolicy | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  useEffect(() => {
    let disposed = false
    void api.execution.get().then(value => {
      if (!disposed) {setStatus(value); setPolicy(value.policy)}
    }).catch(reason => {if (!disposed) setError(String(reason))})
    return () => {disposed = true}
  }, [])
  const save = async () => {
    if (!policy || busy) return
    setBusy(true); setError(''); setNotice('')
    try {
      const next = await api.execution.update(policy)
      setStatus(next); setPolicy(next.policy)
      setNotice('设置已生效。降低并发不会中断正在运行的任务。')
    } catch (reason) {setError(String(reason))}
    finally {setBusy(false)}
  }
  const refresh = async () => {
    setBusy(true); setError('')
    try {setStatus(await api.execution.get())}
    catch (reason) {setError(String(reason))}
    finally {setBusy(false)}
  }
  return <form className="m-0 flex flex-col gap-4 px-6 pt-5" onSubmit={event => {event.preventDefault(); void save()}}>
    {error && <p role="alert" className="m-0 text-sm text-danger">{error}</p>}
    {!policy && !error && <p role="status">正在读取性能设置…</p>}
    {policy && status && <>
      <div className="flex flex-col gap-2 text-sm"><span>调度模式</span><SelectControl ariaLabel="调度模式" value={policy.mode} disabled={busy}
        options={[{value:'auto',label:'自动调节'},{value:'fixed',label:'固定上限'},{value:'serial',label:'串行'}]}
        onChange={mode => setPolicy({...policy,mode:mode as ExecutionPolicy['mode']})}/></div>
      <div className="grid grid-cols-2 gap-4 max-[480px]:grid-cols-1">
        <label className="flex flex-col gap-2 text-sm">页面并发上限<input className={inputClass} type="number" min={1} max={8} required disabled={busy || policy.mode === 'serial'} value={policy.max_pages} onChange={event => setPolicy({...policy,max_pages:Number(event.target.value)})}/></label>
        <label className="flex flex-col gap-2 text-sm">CPU 计算线程上限<input className={inputClass} type="number" min={1} max={8} required disabled={busy || policy.mode === 'serial'} value={policy.cpu_workers} onChange={event => setPolicy({...policy,cpu_workers:Number(event.target.value)})}/></label>
      </div>
      <label className="flex flex-col gap-2 text-sm">保留可用内存（MB）<input className={inputClass} type="number" min={256} max={16384} step={256} required disabled={busy} value={policy.memory_reserve_mb} onChange={event => setPolicy({...policy,memory_reserve_mb:Number(event.target.value)})}/></label>
      <div className="grid grid-cols-2 gap-4 max-[480px]:grid-cols-1">
        <label className="flex flex-col gap-2 text-sm">GPU 并行上限<input className={inputClass} type="number" min={1} max={2} required disabled={busy || policy.mode === 'serial'} value={policy.gpu_max_concurrency ?? 1} onChange={event => setPolicy({...policy,gpu_max_concurrency:Number(event.target.value)})}/></label>
        <label className="flex flex-col gap-2 text-sm">显存预留（MB）<input className={inputClass} type="number" min={2048} max={16384} step={256} required disabled={busy} value={policy.gpu_memory_reserve_mb ?? 2048} onChange={event => setPolicy({...policy,gpu_memory_reserve_mb:Number(event.target.value)})}/></label>
      </div>
      <p className="m-0 text-xs leading-6 text-muted">上限不会绕过模型安全限制，目前实际推理保持 1 路。CTD 和 OCR 批大小至少保留 2 GB 或总显存的 15%，以较大者为准；显存预留可进一步调高。</p>
      <div className="rounded-xl border border-line bg-panel p-4 text-sm leading-7" aria-label="当前执行状态">
        <div className="flex items-center justify-between gap-3"><strong className="font-medium">当前状态</strong><button type="button" className={buttonClass} disabled={busy} onClick={() => void refresh()}>刷新状态</button></div>
        <p className="mb-0">页面：{status.pages.active} / {status.pages.limit} · CPU 计算：{status.cpu.active} / {status.cpu.limit}</p>
        <p className="m-0 text-muted">{status.pages.reason}；{status.cpu.reason}</p>
        <p className="m-0 text-muted">{status.resources.known ? `可用内存 ${(status.resources.available_bytes / 1024 ** 3).toFixed(1)} GB · CPU ${Math.round(status.resources.cpu_percent)}%` : '设备指标不可用，已采用保守限制'}</p>
        <p className="m-0 text-muted">本地模型：{status.models.active} / {status.models.limit} 路</p>
        {status.models.reason ? <p className="m-0 text-muted">{status.models.reason}</p> : null}
        {status.models.calls !== undefined && <p className="m-0 text-muted">累计 {status.models.calls} 次 · 等待 {status.models.wait_seconds?.toFixed(2)} s · 执行 {status.models.run_seconds?.toFixed(2)} s</p>}
        <div aria-label="实际推理后端">
          {status.inference?.length ? status.inference.map(item => <p className="m-0 text-muted" key={`${item.kind}:${item.name}`}>
            {item.name} · {item.backend} · {item.state === 'ready' ? item.device ?? '设备未知' : item.state === 'unloaded' ? '未加载' : item.state === 'loading' ? '加载中' : '加载失败'}
            {item.state === 'ready' && item.fallback_reason ? ` · 回退原因：${fallbackLabels[item.fallback_reason] ?? '加速后端不可用'}` : null}
          </p>) : <p className="m-0 text-muted">尚无已加载的本地推理后端，首次任务运行后显示实际设备。</p>}
        </div>
        {status.gpu?.known ? status.gpu.devices.map(device => <p className="m-0 text-muted" key={device.index}>{device.name} · 显存 {(device.used_bytes / 1024 ** 3).toFixed(1)} / {(device.total_bytes / 1024 ** 3).toFixed(1)} GB{device.utilization_percent === null ? '' : ` · GPU ${Math.round(device.utilization_percent)}%`}</p>) : <p className="m-0 text-muted">GPU 指标不可用，不影响 CPU 或串行处理。</p>}
        {!!status.stage_timings?.length && <details className="mt-2"><summary>最近阶段耗时</summary>{status.stage_timings.map(item => <p className="m-0 text-muted" key={item.stage}>{item.stage} · 平均 {item.mean_seconds.toFixed(2)} s · P95 {item.p95_seconds.toFixed(2)} s · {item.samples} 次</p>)}</details>}
      </div>
      <p className="m-0 text-xs leading-6 text-muted">不同页面可交错处理，同一页面保持有序。本地模型推理暂限 1 路，避免争用 GPU；云端请求仍遵守各自接口限流。CPU 线程用于蒙版等独立计算，不会重复加载模型。</p>
    </>}
    {notice && <p role="status" className="m-0 text-sm text-accent">{notice}</p>}
    <footer className="sticky bottom-0 z-10 -mx-6 flex justify-end gap-3 border-t border-line-subtle bg-surface px-6 py-4"><button type="button" className={buttonClass} onClick={onCancel}>关闭</button><button className={primaryButtonClass} disabled={!policy || busy}>{busy ? '处理中…' : '保存性能设置'}</button></footer>
  </form>
}
