import {describe, expect, it} from 'vitest'
import {DEFAULT_MCP_URL, mcpConfig} from './mcpConfig'

describe('generic MCP configuration', () => {
  it('uses the local MangaFlow endpoint without credentials', () => {
    const config = JSON.parse(mcpConfig('http'))
    expect(DEFAULT_MCP_URL).toBe('http://localhost:8080/mangaflow')
    expect(config.mcpServers.mangaflow).toEqual({type:'http',url:DEFAULT_MCP_URL})
    expect(mcpConfig('http')).not.toContain('MANGAFLOW_MCP_TOKEN')
  })
  it('starts the local connector without requiring a token', () => {
    expect(JSON.parse(mcpConfig('stdio')).mcpServers.mangaflow).toEqual({
      type:'stdio',command:'mangaflow-mcp',env:{MANGAFLOW_MCP_URL:DEFAULT_MCP_URL},
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
})
