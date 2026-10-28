export type MCPTransport = 'http' | 'stdio'

export function mcpEndpoint(publicUrl = '', origin = globalThis.location.origin) {
  return publicUrl || new URL('/mangaflow', origin).href
}

export function mcpSetupUrl(url = mcpEndpoint()) {
  return url.replace(/\/$/, '') + '/setup'
}

export function mcpConfig(transport: MCPTransport, url = mcpEndpoint(), authenticated = false) {
  return JSON.stringify({mcpServers:{mangaflow:transport === 'http'
    ? {type:'http',url,...(authenticated ? {headers:{Authorization:'Bearer ${MANGAFLOW_MCP_TOKEN}'}} : {})}
    : {type:'stdio',command:'mangaflow-mcp',env:{MANGAFLOW_MCP_URL:url,...(authenticated ? {MANGAFLOW_MCP_TOKEN:'${MANGAFLOW_MCP_TOKEN}'} : {})}}}},null,2)
}
