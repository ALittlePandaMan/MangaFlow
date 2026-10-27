export type MCPTransport = 'http' | 'stdio'

export const DEFAULT_MCP_URL = 'http://localhost:8080/mangaflow'

export function mcpSetupUrl(url = DEFAULT_MCP_URL) {
  return url.replace(/\/$/, '') + '/setup'
}

export function mcpConfig(transport: MCPTransport, url = DEFAULT_MCP_URL, authenticated = false) {
  return JSON.stringify({mcpServers:{mangaflow:transport === 'http'
    ? {type:'http',url,...(authenticated ? {headers:{Authorization:'Bearer ${MANGAFLOW_MCP_TOKEN}'}} : {})}
    : {type:'stdio',command:'mangaflow-mcp',env:{MANGAFLOW_MCP_URL:url,...(authenticated ? {MANGAFLOW_MCP_TOKEN:'${MANGAFLOW_MCP_TOKEN}'} : {})}}}},null,2)
}
