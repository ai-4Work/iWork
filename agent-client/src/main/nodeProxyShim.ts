// ===== Node stdio MCP 强制走本地审批代理（网络墙补漏）=====
// 网络墙是协作式：只往子进程 env 注入 HTTP(S)_PROXY，而两类最主流的 Node HTTP 客户端都不读该
// env → 直连外网、代理看不到流量、decideHost 永不触发（见 proxy.ts）：
//   1) Node 内置全局 fetch / undici.request —— 用 undici 全局 dispatcher 槽位
//      （Symbol.for('undici.globalDispatcher.1')，多份 undici 副本共享）强制改道；
//   2) node-fetch / axios / 原生 http(s).request —— 走 node 核心 http(s).globalAgent，把
//      globalAgent 换成 CONNECT 隧道代理 Agent（http(s)-proxy-agent）强制改道。
// EnvHttpProxyAgent 读 process.env 并尊重 NO_PROXY → 内置 fetch 的本地回环不误墙。shim 全程
// try/catch，注入失败 fail-open（直连），绝不因 shim 让 MCP 起不来。
import { createRequire } from 'module'
import { writeFile } from 'fs/promises'
import { tmpdir } from 'os'
import { join } from 'path'
import { getProxyUrl } from './proxy'

const nodeRequire = createRequire(__filename)
const SHIM_FILE = join(tmpdir(), 'agent-desktop-net-shim.cjs')

let cached: Promise<string | null> | null = null

interface ShimPaths {
  undici?: string
  httpProxy?: string
  httpsProxy?: string
}

export function resolveShimPaths(): ShimPaths {
  const paths: ShimPaths = {}
  const add = (name: string, key: keyof ShimPaths): void => {
    try {
      const resolved = nodeRequire.resolve(name)
      if (resolved.includes('app.asar')) {
        console.warn(`[nodeProxyShim] ${name} 位于 app.asar（外部 node 读不到）→ 该层覆盖不可用`)
        return
      }
      paths[key] = resolved
    } catch {
      console.warn(`[nodeProxyShim] 无法解析 ${name} → 该层覆盖不可用`)
    }
  }
  add('undici', 'undici')
  add('http-proxy-agent', 'httpProxy')
  add('https-proxy-agent', 'httpsProxy')
  return paths
}

export function buildShimSource(paths: ShimPaths): string {
  const blocks: string[] = []
  const proxyVar = "process.env.HTTPS_PROXY || process.env.HTTP_PROXY || process.env.ALL_PROXY || ''"

  if (paths.undici) {
    blocks.push(`  try {
    const _undici = require(${JSON.stringify(paths.undici)})
    if (_undici && typeof _undici.setGlobalDispatcher === 'function' && typeof _undici.EnvHttpProxyAgent === 'function') {
      _undici.setGlobalDispatcher(new _undici.EnvHttpProxyAgent())
    }
  } catch (e) {}`)
  }
  if (paths.httpProxy && paths.httpsProxy) {
    blocks.push(`  try {
    const _http = require('http')
    const _https = require('https')
    const { HttpProxyAgent } = require(${JSON.stringify(paths.httpProxy)})
    const { HttpsProxyAgent } = require(${JSON.stringify(paths.httpsProxy)})
    _http.globalAgent = new HttpProxyAgent(_proxyUrl)
    _https.globalAgent = new HttpsProxyAgent(_proxyUrl)
  } catch (e) {}`)
  }
  if (blocks.length === 0) return ''

  return `'use strict'
// 主进程生成：强制 Node stdio MCP 出网走本地审批代理。
const _proxyUrl = ${proxyVar}
if (_proxyUrl) {
${blocks.join('\n')}
}
`
}

async function doEnsure(): Promise<string | null> {
  const source = buildShimSource(resolveShimPaths())
  if (!source) return null
  try {
    await writeFile(SHIM_FILE, source, 'utf-8')
    return SHIM_FILE
  } catch (err) {
    console.warn('[nodeProxyShim] 写 shim 失败 → 跳过注入:', err)
    return null
  }
}

function ensureNodeProxyShim(): Promise<string | null> {
  if (!cached) cached = doEnsure()
  return cached
}

/** 给 stdio MCP spawn env 追加 NODE_OPTIONS=--require=<shim>（追加不覆盖已有值）。代理端口未
 * 就绪或 shim 不可用时静默跳过 → 子进程保持原样（协作式 proxy env，Node fetch 直连）。 */
export async function applyNodeProxyShim(env: Record<string, string>): Promise<void> {
  if (!getProxyUrl()) return
  const shimPath = await ensureNodeProxyShim()
  if (!shimPath) return
  const existing = env.NODE_OPTIONS
  const requireArg = `--require=${shimPath}`
  env.NODE_OPTIONS = existing ? `${existing} ${requireArg}` : requireArg
  // EnvHttpProxyAgent 实验特性会在子进程 stderr 打 [UNDICI-EHPA] 警告（污染 MCP server 日志）
  env.NODE_NO_WARNINGS = '1'
}
