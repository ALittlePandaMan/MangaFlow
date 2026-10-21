import {useState} from 'react'
import {NumberControl, SelectControl} from './FormControls'
import {cn, inputClass, textareaClass} from '../ui'
import {SettingsSection} from './SettingsControls'
import {ApiKeyInput} from './ApiKeyInput'

const fieldClass = 'min-w-0 text-sm text-secondary [&>*:last-child]:mt-2'

export function GenericOCRFields({defaults, baseUrl, onBaseUrlChange, savedKeyUsable, apiKey, onApiKeyChange}: {
  defaults: Record<string, unknown>
  baseUrl: string
  onBaseUrlChange: (url: string) => void
  savedKeyUsable: boolean
  apiKey: string
  onApiKeyChange: (value: string) => void
}) {
  const [format, setFormat] = useState(String(defaults.request_format || 'json'))
  const [encoding, setEncoding] = useState(String(defaults.image_encoding || 'base64'))
  const [auth, setAuth] = useState(String(defaults.auth_mode || 'bearer'))
  return <SettingsSection title="专用接口配置"><div className="settings-grid">
    <p className="col-span-full settings-privacy">执行 OCR 时，裁剪图片会发送到所配置的服务，可能产生费用。</p>
    <label className={cn(fieldClass, 'col-span-full')}>识别接口 URL<input className={inputClass} name="base_url" type="url" required value={baseUrl} onChange={event => onBaseUrlChange(event.target.value)} placeholder="https://ocr.example.com/v1/recognize"/></label>
    <div className={cn(fieldClass, 'col-span-full')}><span>图片上传方式</span><SelectControl name="request_format" ariaLabel="图片上传方式" value={format} onChange={setFormat} options={[{value:'chat_completions', label:'OCR 专用模型（Chat Completions）'}, {value:'json', label:'JSON + Base64'}, {value:'multipart', label:'Multipart 文件上传'}]}/></div>
    {format !== 'chat_completions' && <label className={fieldClass}>图片字段名<input className={inputClass} name="image_field" required defaultValue={String(defaults.image_field || 'image')} placeholder="image 或 file"/></label>}
    {format === 'chat_completions' && <>
      <label className={cn(fieldClass, 'col-span-full')}>OCR 模型名称<input className={inputClass} name="model" required defaultValue={String(defaults.model || '')} placeholder="PaddlePaddle/PaddleOCR-VL-1.5"/></label>
      <label className={cn(fieldClass, 'col-span-full')}>识别指令<textarea className={cn(textareaClass, 'min-h-20')} name="prompt" defaultValue={String(defaults.prompt || 'OCR:')}/></label>
      <label className={cn(fieldClass, 'col-span-full')}>最大输出 Token 数<NumberControl ariaLabel="最大输出 Token 数" name="max_tokens" min={128} max={32768} step={128} defaultValue={String(defaults.max_tokens ?? 2048)}/></label>
    </>}
    {format === 'json' && <div className={cn(fieldClass, 'col-span-full')}><span>图片编码</span><SelectControl name="image_encoding" ariaLabel="图片编码" value={encoding} onChange={setEncoding} options={[{value:'base64', label:'纯 Base64'}, {value:'data_url', label:'Data URL（带图片类型前缀）'}]}/></div>}
    <div className={fieldClass}><span>鉴权方式</span><SelectControl name="auth_mode" ariaLabel="鉴权方式" value={auth} onChange={setAuth} options={[{value:'bearer', label:'Bearer Token'}, {value:'api_key', label:'API Key 请求头'}, {value:'none', label:'无需鉴权'}]}/></div>
    {auth === 'api_key' && <label className={fieldClass}>密钥请求头<input className={inputClass} name="api_key_header" required defaultValue={String(defaults.api_key_header || 'X-API-Key')}/></label>}
    {auth !== 'none' && <label className={cn(fieldClass, 'col-span-full')}>API Key<ApiKeyInput name="api_key" hasSavedKey={savedKeyUsable} required={!savedKeyUsable} value={apiKey} onChange={event => onApiKeyChange(event.target.value)} placeholder="填写此 OCR 服务的密钥"/></label>}
    {format !== 'chat_completions' && <><label className={cn(fieldClass, 'col-span-full')}>返回文字字段路径<input className={inputClass} name="response_text_path" required defaultValue={String(defaults.response_text_path || 'text')} placeholder="data.words.*.text"/></label>
    </>}
    <label className={cn(fieldClass, 'col-span-full')}>附加参数（JSON）<textarea aria-label="附加参数（JSON）" className={cn(textareaClass, 'min-h-24 font-mono')} name="extra_fields" defaultValue={JSON.stringify(defaults.extra_fields || {}, null, 2)} placeholder={'{"language":"ja","model":"ocr-model"}'}/></label>
    <p className="col-span-full settings-privacy">密钥请填写在 API Key 字段，不要写入附加参数。</p>
    <label className={fieldClass}>超时（秒）<NumberControl ariaLabel="接口超时" name="timeout" min={10} max={600} step={5} defaultValue={String(defaults.timeout ?? 90)}/></label>
    <label className={fieldClass}>失败重试<NumberControl ariaLabel="失败重试次数" name="retries" min={0} max={5} step={1} defaultValue={String(defaults.retries ?? 2)}/></label>
  </div></SettingsSection>
}
