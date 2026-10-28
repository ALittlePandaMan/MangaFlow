import {afterEach, beforeEach, describe, expect, it, vi} from 'vitest'
import {mcpEndpoint, mcpConfig, mcpSetupUrl} from './mcpConfig'

beforeEach(() => vi.stubGlobal('location', {origin:'http://localhost:18080'}))
afterEach(() => vi.unstubAllGlobals())

describe('generic MCP configuration', () => {
  it('uses the local MangaFlow endpoint without credentials', () => {
    const config = JSON.parse(mcpConfig('http'))
    expect(config.mcpServers.mangaflow).toEqual({type:'http',url:'http://localhost:18080/mangaflow'})
    expect(mcpConfig('http')).not.toContain('MANGAFLOW_MCP_TOKEN')
  })
  it('starts the local connector without requiring a token', () => {
    expect(JSON.parse(mcpConfig('stdio')).mcpServers.mangaflow).toEqual({
      type:'stdio',command:'mangaflow-mcp',env:{MANGAFLOW_MCP_URL:'http://localhost:18080/mangaflow'},
    })
  })
  it.each(['http','stdio'] as const)('retains remote authentication without embedding a secret in %s', transport => {
    const snippet=mcpConfig(transport,'https://manga.example/mangaflow',true)
    expect(snippet).toContain('MANGAFLOW_MCP_TOKEN')
    expect(JSON.parse(snippet).mcpServers.mangaflow.type).toBe(transport)
  })
  it('escapes the configured address as JSON', () => {
    const url='https://manga.example/"\n'
    expect(JSON.parse(mcpConfig('http',url)).mcpServers.mangaflow.url).toBe(url)
  })
  it.each(['http://localhost:8080','http://localhost:18080','http://127.0.0.1:19090',
    'http://[::1]:28080','https://manga.example:9443','https://manga.example'])('preserves the actual site origin %s', origin => {
    const endpoint=mcpEndpoint('',origin)
    expect(endpoint).toBe(`${origin}/mangaflow`)
    expect(mcpSetupUrl(endpoint)).toBe(`${origin}/mangaflow/setup`)
    expect(JSON.parse(mcpConfig('http',endpoint)).mcpServers.mangaflow.url).toBe(endpoint)
    expect(JSON.parse(mcpConfig('stdio',endpoint)).mcpServers.mangaflow.env.MANGAFLOW_MCP_URL).toBe(endpoint)
  })
  it('prefers an explicit public URL and recomputes the default at call time', () => {
    expect(mcpEndpoint('https://remote.example:9443/mcp','http://localhost:18080')).toBe('https://remote.example:9443/mcp')
    expect(mcpSetupUrl()).toBe('http://localhost:18080/mangaflow/setup')
    vi.stubGlobal('location',{origin:'http://localhost:28080'})
    expect(JSON.parse(mcpConfig('http')).mcpServers.mangaflow.url).toBe('http://localhost:28080/mangaflow')
  })
})
