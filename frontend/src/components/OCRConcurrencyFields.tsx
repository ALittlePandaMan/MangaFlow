import {useState} from 'react'
import {NumberControl, SelectControl} from './FormControls'

export function OCRConcurrencyFields({defaults}: {defaults: Record<string, unknown>}) {
  const [mode, setMode] = useState(String(defaults.concurrency_mode || 'adaptive'))
  return <fieldset className="col-span-full m-0 min-w-0 rounded-xl border border-line p-4">
    <legend className="px-1 text-xs font-semibold text-secondary">云端识别并发</legend>
    <div className="grid grid-cols-3 gap-3 max-sm:grid-cols-1">
      <div className="min-w-0 text-xs text-secondary"><span className="mb-2 block">调度模式</span><SelectControl name="concurrency_mode" ariaLabel="OCR 并发模式" value={mode} onChange={setMode} options={[{value:'adaptive', label:'自适应并发（推荐）'}, {value:'fixed', label:'固定并发'}, {value:'serial', label:'串行识别'}]}/></div>
      {mode !== 'serial' && <label className="min-w-0 text-xs text-secondary">{mode === 'fixed' ? '并发数量' : '并发上限'}<div className="mt-2"><NumberControl name="max_concurrency" ariaLabel="OCR 并发上限" min={1} max={16} step={1} defaultValue={String(defaults.max_concurrency ?? 8)}/></div></label>}
      <label className="min-w-0 text-xs text-secondary">请求上限（次/分钟）<div className="mt-2"><NumberControl name="requests_per_minute" ariaLabel="OCR 每分钟请求上限" min={1} max={6000} step={1} defaultValue={String(defaults.requests_per_minute ?? 60)}/></div></label>
    </div>
    <p className="mb-0 mt-3 text-xs leading-relaxed text-muted">{mode === 'adaptive' ? '从 2 路开始，使用实际任务逐步调整；限流、超时或提速无收益时降速。不会额外上传图片测试上限。' : mode === 'fixed' ? '按设定数量同时识别；遇到限流仍会统一等待，再重试失败区域。' : '每次只识别一个区域；适合限额较低的接口。'} 默认每分钟最多 60 次（含重试），请按服务商配额调整。相同接口域名、密钥和模型的 OCR 与翻译共享速率及退避；多密钥账号配额、Token 限额仍以服务端为准。</p>
  </fieldset>
}
