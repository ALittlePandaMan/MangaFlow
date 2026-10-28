import {fireEvent, render, screen, waitFor} from '@testing-library/react'
import {afterEach, beforeEach, describe, expect, it, vi} from 'vitest'
import {changeLocale} from '../i18n'
import {MCPSettings} from './MCPSettings'

const {alert} = vi.hoisted(() => ({alert:vi.fn()}))
vi.mock('./GlobalAlert', () => ({useGlobalAlert:() => ({alert})}))

beforeEach(async () => {
  await changeLocale('en')
  alert.mockClear()
  vi.stubGlobal('location',{origin:'http://localhost:18080'})
  vi.stubGlobal('fetch',vi.fn().mockResolvedValue({ok:true,json:async () => ({enabled:true,public_url:'',token:'local-secret'})}))
})
afterEach(() => {vi.unstubAllGlobals()})

describe('generic MCP settings', () => {
  it('shows a content-sized code block without client or local token controls', async () => {
    const {container}=render(<MCPSettings/>)
    expect(await screen.findByLabelText('Connection URL')).toHaveValue('http://localhost:18080/mangaflow')
    expect(screen.queryByLabelText('Client')).not.toBeInTheDocument()
    expect(screen.queryByLabelText('Access token')).not.toBeInTheDocument()
    expect(container.querySelector('textarea')).toBeNull()
    const code=container.querySelector('pre.settings-code')!
    expect(JSON.parse(code.textContent!).mcpServers.mangaflow).toEqual({type:'stdio',command:'mangaflow-mcp',env:{MANGAFLOW_MCP_URL:'http://localhost:18080/mangaflow'}})
    expect(code).not.toHaveTextContent('MANGAFLOW_MCP_TOKEN')
  })
  it('tests the canonical endpoint without sending a token for local access', async () => {
    render(<MCPSettings/>)
    const test=await screen.findByRole('button',{name:'Test connection'})
    vi.mocked(fetch).mockResolvedValueOnce({ok:true,json:async () => ({result:{serverInfo:{name:'MangaFlow'}}})} as Response)
    fireEvent.click(test)
    await waitFor(() => expect(alert).toHaveBeenCalledWith({message:'MCP connection successful',tone:'success'}))
    expect(fetch).toHaveBeenLastCalledWith('/mangaflow',expect.objectContaining({headers:{'Content-Type':'application/json',Accept:'application/json, text/event-stream'}}))
  })
  it('switches one advanced configuration panel and copies the selected transport', async () => {
    const writeText=vi.fn().mockResolvedValue(undefined)
    vi.stubGlobal('navigator',{clipboard:{writeText}})
    const {container}=render(<MCPSettings/>)
    await screen.findByLabelText('Connection URL')
    expect(screen.queryByRole('button',{name:'Transport'})).not.toBeInTheDocument()
    expect(screen.queryByLabelText('HTTP')).not.toBeInTheDocument()
    expect(screen.getByLabelText('Connection settings')).not.toBeVisible()
    fireEvent.click(screen.getByText('Advanced connections'))
    expect(screen.getByRole('link',{name:'Download connector'})).toBeVisible()
    const config=JSON.parse(container.querySelector('pre')!.textContent!)
    expect(config.mcpServers.mangaflow).toEqual({type:'stdio',command:'mangaflow-mcp',env:{MANGAFLOW_MCP_URL:'http://localhost:18080/mangaflow'}})
    const local=screen.getByRole('tab',{name:'Local connector'})
    fireEvent.keyDown(local,{key:'ArrowRight'})
    const http=screen.getByRole('tab',{name:'HTTP'})
    expect(http).toHaveAttribute('aria-selected','true')
    expect(http).toHaveFocus()
    expect(container.querySelectorAll('pre')).toHaveLength(1)
    const httpConfig=screen.getByRole('tabpanel',{name:'HTTP'}).textContent!
    expect(JSON.parse(httpConfig).mcpServers.mangaflow).toEqual({type:'http',url:'http://localhost:18080/mangaflow'})
    expect(screen.queryByRole('link',{name:'Download connector'})).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button',{name:'Copy HTTP configuration'}))
    await waitFor(() => expect(writeText).toHaveBeenCalledWith(httpConfig))
    fireEvent.keyDown(http,{key:'Home'})
    expect(local).toHaveAttribute('aria-selected','true')
    expect(local).toHaveFocus()
    expect(screen.getByRole('link',{name:'Download connector'})).toBeVisible()
  })
  it('copies a complete installation request with a directly readable guide URL', async () => {
    const writeText=vi.fn().mockResolvedValue(undefined)
    vi.stubGlobal('navigator',{clipboard:{writeText}})
    render(<MCPSettings/>)
    fireEvent.click(await screen.findByRole('button',{name:'Copy installation instructions'}))
    await waitFor(() => expect(writeText).toHaveBeenCalledWith(expect.stringContaining('http://localhost:18080/mangaflow/setup')))
    expect(writeText).toHaveBeenCalledWith(expect.stringContaining('Preserve existing MCP servers'))
    expect(writeText).not.toHaveBeenCalledWith(expect.stringContaining('local-secret'))
    expect(screen.getByRole('link',{name:'Installation guide'})).toHaveAttribute('href','http://localhost:18080/mangaflow/setup')
  })
  it('uses the configured remote service for installation without copying credentials', async () => {
    vi.mocked(fetch).mockResolvedValueOnce({ok:true,json:async () => ({enabled:true,public_url:'https://manga.example/mangaflow',token:'private-token'})} as Response)
    const writeText=vi.fn().mockResolvedValue(undefined)
    vi.stubGlobal('navigator',{clipboard:{writeText}})
    render(<MCPSettings/>)
    fireEvent.click(await screen.findByRole('button',{name:'Copy installation instructions'}))
    expect(screen.getByLabelText('Connection URL')).toHaveValue('https://manga.example/mangaflow')
    await waitFor(() => expect(writeText).toHaveBeenCalledWith(expect.stringContaining('https://manga.example/mangaflow/setup')))
    expect(writeText).not.toHaveBeenCalledWith(expect.stringContaining('private-token'))
  })
  it('does not persist the inferred deployment address when saving settings', async () => {
    render(<MCPSettings/>)
    fireEvent.click(await screen.findByRole('button',{name:'Save configuration'}))
    await waitFor(() => expect(fetch).toHaveBeenCalledWith('/api/mcp-settings',expect.objectContaining({
      method:'PUT',body:JSON.stringify({enabled:true,public_url:''}),
    })))
    expect(screen.getByLabelText('Connection URL')).toHaveValue('http://localhost:18080/mangaflow')
  })
})
