import {useState} from 'react'
import {NumberControl, SelectControl} from './FormControls'

type ClassificationConfig = {enabled?: boolean; filter_sfx?: boolean; min_sfx_confidence?: number}

export function TextClassificationFields({config}: {config: unknown}) {
  const options = config && typeof config === 'object' ? config as ClassificationConfig : {}
  const initialMode = config === false || options.enabled === false ? 'off' : options.filter_sfx === false ? 'keep' : 'filter'
  const [mode, setMode] = useState(initialMode)
  return <fieldset className="col-span-full m-0 min-w-0 rounded-lg border border-line p-3">
    <legend className="px-1 text-[11px] font-medium text-ink">文字分类</legend>
    <div className="grid grid-cols-2 gap-3 max-sm:grid-cols-1">
      <div className="min-w-0 text-[10px] text-secondary">
        <span>拟声词处理</span>
        <div className="mt-2"><SelectControl name="classification_mode" ariaLabel="拟声词处理" value={mode} options={[
          {value: 'filter', label: '分类并过滤拟声词'},
          {value: 'keep', label: '分类但保留拟声词'},
          {value: 'off', label: '关闭分类'},
        ]} onChange={setMode}/></div>
      </div>
      <label className="min-w-0 text-[10px] text-secondary">拟声词过滤置信度
        <div className="mt-2"><NumberControl name="sfx_confidence" ariaLabel="拟声词过滤置信度" min={0.4} max={0.95} step={0.05} disabled={mode === 'off'} defaultValue={String(options.min_sfx_confidence ?? 0.6)}/></div>
      </label>
    </div>
    <p className="mb-0 mt-3 text-[10px] leading-relaxed text-muted">本地分类普通文字与拟声词。不确定的文字和气泡内对话保留。切换为“保留拟声词”后重新 OCR，可重新检测被过滤的框。</p>
  </fieldset>
}
