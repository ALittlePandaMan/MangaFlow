import {t, useLocale} from '../i18n'
import {useEffect, useState} from 'react'
import {RefreshCw} from 'lucide-react'
import {api} from '../services/api'
import type {ExecutionPolicy, ExecutionStatus} from '../types'
import {buttonClass, inputClass} from '../ui'
import {SelectControl} from './FormControls'
import {SettingsField, SettingsSection} from './SettingsControls'
import {SettingsFormSkeleton} from './SettingsSkeleton'
import {ButtonLoading} from './LoadingUI'
import {useGlobalAlert} from './GlobalAlert'

const fallbackLabels: Record<string, string> = {
  get cuda_provider_unavailable(){return t("CUDA 推理后端不可用")},
  get cuda_memory_reserve(){return t("显存余量不足")},
  get cuda_provider_initialization_failed(){return t("CUDA 推理后端初始化失败")},
  get cuda_initialization_failed(){return t("CUDA 模型初始化失败")},
  get cuda_inference_failed(){return t("CUDA 推理失败")},
  get accelerator_unavailable(){return t("加速设备不可用")},
  get accelerator_initialization_failed(){return t("加速模型初始化失败")},
  get accelerator_inference_failed(){return t("加速推理失败")},
}

export function ExecutionSettings({onCancel}: {onCancel?: () => void}) {
  useLocale()
  const {alert} = useGlobalAlert()
  const [status, setStatus] = useState<ExecutionStatus | null>(null)
  const [policy, setPolicy] = useState<ExecutionPolicy | null>(null)
  const [busy, setBusy] = useState(false)
  const [refreshing, setRefreshing] = useState(false)
  const [error, setError] = useState('')
  useEffect(() => {
    let disposed = false
    void api.execution.get().then(value => {
      if (!disposed) {setStatus(value); setPolicy(value.policy)}
    }).catch(reason => {if (!disposed) setError(String(reason))})
    return () => {disposed = true}
  }, [])
  const save = async () => {
    if (!policy || busy || refreshing) return
    setBusy(true)
    try {
      const next = await api.execution.update(policy)
      setStatus(next); setPolicy(next.policy)
      void alert({title:t("保存成功"),message:t("设置已生效。"),tone:'success'})
    } catch (reason) {
      const message=reason instanceof Error ? reason.message : String(reason)
      void alert({title:t("保存失败"),message,tone:'danger'})
    }
    finally {setBusy(false)}
  }
  const refresh = async () => {
    if (busy || refreshing) return
    setRefreshing(true); setError('')
    try {
      const next = await api.execution.get()
      setStatus(next); setPolicy(current => current ?? next.policy)
    }
    catch (reason) {
      const message=reason instanceof Error ? reason.message : String(reason)
      if (!policy) setError(message)
      else void alert({title:t("刷新状态"),message,tone:'danger'})
    }
    finally {setRefreshing(false)}
  }
  if (!policy) return <>
    {error ? <div className="settings-error-state"><p role="alert" className="settings-error">{error}</p><button type="button" className={buttonClass} disabled={refreshing} onClick={() => void refresh()}>{refreshing ? <ButtonLoading label={t("正在重新读取…")}/> : <><RefreshCw aria-hidden="true" size={16}/>{t("重新读取")}</>}</button></div> : <SettingsFormSkeleton label={t("正在读取性能设置…")} sections={[2,5]}/>}
  </>
  return <form className="settings-form settings-performance" onSubmit={event => {event.preventDefault(); void save()}}>
    {policy && status && <>
      <SettingsSection title={t("调度与资源上限")}>
        <SettingsField label={t("调度模式")}><SelectControl ariaLabel={t("调度模式")} value={policy.mode} disabled={busy} options={[{value:'auto',label:t("自动调节")},{value:'fixed',label:t("固定上限")},{value:'serial',label:t("串行")}]} onChange={mode => setPolicy({...policy,mode:mode as ExecutionPolicy['mode']})}/></SettingsField>
        <div className="settings-grid settings-grid-three">
          <label>{t("页面并发上限")}<input className={inputClass} type="number" min={1} max={8} required disabled={busy || policy.mode === 'serial'} value={policy.max_pages} onChange={event => setPolicy({...policy,max_pages:Number(event.target.value)})}/></label>
          <label>{t("CPU 计算线程上限")}<input className={inputClass} type="number" min={1} max={8} required disabled={busy || policy.mode === 'serial'} value={policy.cpu_workers} onChange={event => setPolicy({...policy,cpu_workers:Number(event.target.value)})}/></label>
          <label>{t("保留可用内存（MB）")}<input className={inputClass} type="number" min={256} max={16384} step={256} required disabled={busy} value={policy.memory_reserve_mb} onChange={event => setPolicy({...policy,memory_reserve_mb:Number(event.target.value)})}/></label>
        </div>
        <div className="settings-grid">
          <label>{t("GPU 并行上限")}<input className={inputClass} type="number" min={1} max={2} required disabled={busy || policy.mode === 'serial'} value={policy.gpu_max_concurrency ?? 1} onChange={event => setPolicy({...policy,gpu_max_concurrency:Number(event.target.value)})}/></label>
          <label>{t("显存预留（MB）")}<input className={inputClass} type="number" min={2048} max={16384} step={256} required disabled={busy} value={policy.gpu_memory_reserve_mb ?? 2048} onChange={event => setPolicy({...policy,gpu_memory_reserve_mb:Number(event.target.value)})}/></label>
        </div>
      </SettingsSection>
      <SettingsSection title={t("当前运行状态")}>
        <div className="settings-runtime-header"><span className="settings-readonly">{t("页面")}{' '}{status.pages.active} / {status.pages.limit} · CPU {status.cpu.active} / {status.cpu.limit}</span><button type="button" className={buttonClass} disabled={busy || refreshing} onClick={() => void refresh()}>{refreshing ? <ButtonLoading label={t("正在刷新…")}/> : <><RefreshCw aria-hidden="true" size={16}/>{t("刷新状态")}</>}</button></div>
        <table className="settings-runtime-table" aria-label={t("实际推理后端")}><thead><tr><th>{t("本地模型")}</th><th>{t("状态")}</th><th>{t("实际设备")}</th></tr></thead><tbody>
          {status.inference?.length ? status.inference.map(item => <tr key={item.kind+':'+item.name}><td>{item.name}</td><td>{item.state === 'ready' ? t("已就绪") : item.state === 'unloaded' ? t("未加载") : item.state === 'loading' ? t("加载中") : t("加载失败")}</td><td>{item.device || '—'}{item.fallback_reason ? ' · '+(fallbackLabels[item.fallback_reason] ?? t("加速后端不可用")) : ''}</td></tr>) : <tr><td colSpan={3}>{t("尚无已加载的本地推理后端")}</td></tr>}
        </tbody></table>
        <details className="settings-runtime-detail" aria-label={t("当前执行状态")}><summary>{t("资源与阶段耗时")}</summary>
          <p>{t(status.pages.reason)}; {t(status.cpu.reason)}</p>
          <p>{status.resources.known ? t("可用内存 ")+(status.resources.available_bytes / 1024 ** 3).toFixed(1)+' GB · CPU '+Math.round(status.resources.cpu_percent)+'%' : t("设备指标不可用")}</p>
          <p>{t("本地模型：")}{status.models.active} / {status.models.limit} {' '}{t("路")}</p>
          {status.models.reason && <p>{t(status.models.reason)}</p>}
          {status.models.calls !== undefined && <p>{t("累计")}{' '}{status.models.calls} {' '}{t("次 · 等待")}{' '}{status.models.wait_seconds?.toFixed(2)} {' '}{t("s · 执行")}{' '}{status.models.run_seconds?.toFixed(2)} s</p>}
          {status.inference?.map(item => <p key={item.kind+':'+item.name}>{item.name} · {item.backend} · {item.state === 'ready' ? item.device ?? t("设备未知") : item.state === 'unloaded' ? t("未加载") : item.state === 'loading' ? t("加载中") : t("加载失败")}{item.state === 'ready' && item.fallback_reason ? t(" · 回退原因：")+(fallbackLabels[item.fallback_reason] ?? t("加速后端不可用")) : null}</p>)}
          {status.gpu?.known && status.gpu.devices.map(device => <p key={device.index}>{device.name} {' '}{t("· 显存")}{' '}{(device.used_bytes / 1024 ** 3).toFixed(1)} / {(device.total_bytes / 1024 ** 3).toFixed(1)} GB{device.utilization_percent === null ? '' : ' · GPU '+Math.round(device.utilization_percent)+'%'}</p>)}
          {status.stage_timings?.map(item => <p key={item.stage}>{item.stage} {' '}{t("· 平均")}{' '}{item.mean_seconds.toFixed(2)} s · P95 {item.p95_seconds.toFixed(2)} s · {item.samples} {' '}{t("次")}</p>)}
        </details>
      </SettingsSection>
    </>}
    <footer className="settings-actions">{onCancel && <button type="button" className={buttonClass} onClick={onCancel}>{t("关闭")}</button>}<button className={buttonClass+' settings-primary'} disabled={busy || refreshing}>{busy ? <ButtonLoading label={t("正在保存…")}/> : t("保存性能设置")}</button></footer>
  </form>
}
