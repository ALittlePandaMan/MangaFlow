import {useEffect, useId, useState} from 'react'
import {ChevronRight, Copy, Download, Globe, RefreshCw, Terminal} from 'lucide-react'
import {t} from '../i18n'
import {buttonClass, inputClass} from '../ui'
import {SettingsField, SettingsSection} from './SettingsControls'
import {useGlobalAlert} from './GlobalAlert'
import {ApiKeyInput} from './ApiKeyInput'
import {DEFAULT_MCP_URL, mcpConfig, mcpSetupUrl, type MCPTransport} from '../features/settings/mcpConfig'

interface Configuration {enabled: boolean; public_url: string; token: string}
async function request(path: string, init?: RequestInit): Promise<Configuration> {
  const response = await fetch('/api/mcp-settings'+path, init)
  const body = await response.json()
  if (!response.ok) throw new Error(body.detail || response.statusText)
  return body
}

export function MCPSettings() {
  const [config,setConfig] = useState<Configuration | null>(null)
  const [busy,setBusy] = useState(false)
  const [transport,setTransport] = useState<MCPTransport>('stdio')
  const configId = useId()
  const {alert} = useGlobalAlert()
  useEffect(() => {
    let active = true
    request('').then(value => {if (active) setConfig(value)}).catch(error => {if (active) alert({message:error.message,tone:'danger'})})
    return () => {active=false}
  },[alert])
  const execute = async (work: () => Promise<void>) => {
    setBusy(true)
    try {await work()} catch (error) {alert({message:error instanceof Error ? error.message : String(error),tone:'danger'})}
    finally {setBusy(false)}
  }
  const copy = async (text: string) => {await navigator.clipboard.writeText(text); alert({message:t('已复制'),tone:'success'})}
  if (!config) return <button className={buttonClass} onClick={() => void execute(async () => setConfig(await request('')))}>{t('重新加载')}</button>
  const url = config.public_url || DEFAULT_MCP_URL
  const remote = Boolean(config.public_url)
  const snippet=mcpConfig(transport,url,remote)
  const setupUrl=mcpSetupUrl(url)
  const installInstruction=t('请按照 {{url}} 的官方安装说明，为当前 Agent 安装并配置 MangaFlow 本地 MCP 连接程序。保留已有 MCP 配置，完成后重新连接并通过 mcp_status 验证本机目录导入、上传和下载能力。', {url:setupUrl})
  return <div className="settings-form">
    <SettingsSection title={t('连接配置')}>
      <SettingsField label="MCP">
        <label className="settings-style-toggle"><input type="checkbox" checked={config.enabled} disabled={busy} onChange={event => setConfig({...config,enabled:event.target.checked})}/>{t('启用 MCP')}</label>
      </SettingsField>
      <SettingsField label={t('远程服务地址')}>
        <input type="url" aria-label={t('远程服务地址')} className={inputClass} placeholder="https://manga.example/mangaflow" value={config.public_url} onChange={event => setConfig({...config,public_url:event.target.value})}/>
      </SettingsField>
      <SettingsField label={t('连接地址')}>
        <input aria-label={t('连接地址')} className={inputClass} readOnly value={url}/>
      </SettingsField>
      {remote && <SettingsField label={t('访问密钥')}>
        <div className="settings-inline-actions">
          <div className="settings-inline-control"><ApiKeyInput aria-label={t('访问密钥')} value={config.token} hasSavedKey readOnly/></div>
          <button type="button" className={buttonClass} onClick={() => void execute(() => copy(config.token))}><Copy size={16}/>{t('复制密钥')}</button>
          <button type="button" className={buttonClass} disabled={busy} onClick={() => void execute(async () => {setConfig(await request('/rotate',{method:'POST'}));alert({message:t('密钥已重置'),tone:'success'})})}><RefreshCw size={16}/>{t('重置密钥')}</button>
        </div>
      </SettingsField>}
    </SettingsSection>
    <SettingsSection title={t('通用接入')}>
      <div className="settings-inline-actions">
        <button type="button" className={buttonClass} onClick={() => void execute(() => copy(installInstruction))}><Copy size={16}/>{t('复制安装指令')}</button>
        <a className={buttonClass} href={setupUrl} target="_blank" rel="noreferrer">{t('安装说明')}</a>
      </div>
    </SettingsSection>
    <details className="settings-advanced">
      <summary><ChevronRight size={16}/>{t('高级接入')}</summary>
      <div className="settings-section-body">
        <div className="settings-mcp-config">
          <div className="settings-mcp-config-toolbar">
            <div className="settings-mcp-tabs" role="tablist" aria-label={t('连接方式')}>
              {(['stdio','http'] as const).map(mode => <button key={mode} type="button" role="tab"
                id={`${configId}-${mode}`} aria-controls={`${configId}-panel`} aria-selected={transport === mode}
                tabIndex={transport === mode ? 0 : -1} onClick={() => setTransport(mode)}
                onKeyDown={event => {
                  const next = event.key === 'Home' ? 'stdio' : event.key === 'End' ? 'http'
                    : event.key === 'ArrowLeft' || event.key === 'ArrowRight' ? (mode === 'stdio' ? 'http' : 'stdio') : null
                  if (!next) return
                  event.preventDefault()
                  setTransport(next)
                  event.currentTarget.parentElement?.querySelectorAll<HTMLButtonElement>('[role="tab"]')[next === 'stdio' ? 0 : 1]?.focus()
                }}>
                {mode === 'stdio' ? <Terminal size={16} aria-hidden="true"/> : <Globe size={16} aria-hidden="true"/>}
                {mode === 'stdio' ? t('本地连接程序') : 'HTTP'}
              </button>)}
            </div>
            <div className="settings-mcp-config-actions">
              {transport === 'stdio' && <a className={buttonClass} href="/api/mcp-settings/connector" download aria-label={t('下载连接程序')} title={t('下载连接程序')}>
                <Download size={16} aria-hidden="true"/><span>{t('下载连接程序')}</span>
              </a>}
              <button type="button" className={buttonClass} disabled={busy}
                aria-label={transport === 'stdio' ? t('复制配置') : t('复制 HTTP 配置')}
                title={transport === 'stdio' ? t('复制配置') : t('复制 HTTP 配置')}
                onClick={() => void execute(() => copy(snippet))}><Copy size={16} aria-hidden="true"/><span>{t('复制配置')}</span></button>
            </div>
          </div>
          <div role="tabpanel" id={`${configId}-panel`} aria-labelledby={`${configId}-${transport}`}>
            <pre className="settings-code" aria-label={transport === 'stdio' ? t('连接配置') : 'HTTP'} tabIndex={0}><code>
              {snippet.split(/("(?:\\.|[^"\\])*"(?:\s*:)?)/g).map((token,index) => token.startsWith('"')
                ? <span key={index} className={token.endsWith(':') ? 'settings-mcp-code-key' : 'settings-mcp-code-string'}>{token}</span> : token)}
            </code></pre>
          </div>
        </div>
      </div>
    </details>
    <footer className="settings-actions"><button type="button" className={buttonClass} disabled={busy} onClick={() => void execute(async () => {
      const response = await fetch('/mangaflow',{method:'POST',headers:{'Content-Type':'application/json',Accept:'application/json, text/event-stream',...(remote ? {Authorization:`Bearer ${config.token}`} : {})},
        body:JSON.stringify({jsonrpc:'2.0',id:1,method:'initialize',params:{protocolVersion:'2025-11-25',capabilities:{},clientInfo:{name:'MangaFlow Settings',version:'1.0.0'}}})})
      const body = await response.json()
      if (!response.ok || body.error || !body.result?.serverInfo) throw new Error(body.error?.message || body.error || t('连接失败'))
      alert({message:t('MCP 连接成功'),tone:'success'})
    })}>{t('测试连接')}</button><button type="button" className={`${buttonClass} settings-primary`} disabled={busy} onClick={() => void execute(async () => {
      setConfig(await request('',{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({enabled:config.enabled,public_url:config.public_url})}))
      alert({message:t('配置已保存'),tone:'success'})
    })}>{t('保存配置')}</button></footer>
  </div>
}
