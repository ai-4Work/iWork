import { createServer, request as httpRequest, type Server } from 'http'
import { request as httpsRequest } from 'https'
import { connect as netConnect } from 'net'
import { tmpdir } from 'os'
import { ipcMain, type BrowserWindow } from 'electron'
import { getSessionTempDir, setExtraWritableRoots } from './sandbox'

// ===== P2 本地 HTTP 代理 + 域名审批（§8.6）=====
// Node 内置 http 模块，零依赖。只判 host，不做 MITM/TLS 解密：
// - CONNECT（HTTPS）→ 提取 host → 判定 → 200 后 TCP 隧道对通 / 403
// - absolute-form（HTTP）→ 提取 host → 判定 → 放行转发 / 403
// 通过 HTTPS_PROXY/HTTP_PROXY/ALL_PROXY 注入 bash/MCP spawn env，不碰系统代理。

export interface NetworkApproval {
  host: string
  protocol: string
  decision: 'allow' | 'deny'
  approved: boolean
}

interface NetworkPolicy {
  enabled?: boolean
  allow_domains?: string[]
  deny_domains?: string[]
  unknown_domain?: string
  approval_policy?: string | Record<string, unknown>
  network_rules?: Array<{ host: string; protocol: string; decision: 'allow' | 'prompt' | 'forbidden' }>
}

/** 服务端 build_policy 下发的整包（renderer PolicyPacket 的主进程镜像），供 P3 沙箱读 sandbox/filesystem */
export interface ProxyPolicyPacket {
  filesystem?: { allow_write?: string[]; deny_write?: string[]; deny_read?: string[] }
  network?: NetworkPolicy
  sandbox?: { required?: boolean; exempt_commands?: string[] }
}

/** 服务端下发时保留的临时目录标记（服务端无法得知本机用户名的临时目录），收到策略包时本地解析 */
const TMPDIR_MARK = '@TMPDIR@'

/** 解析策略包里的 @TMPDIR@ 标记。allow_write 指向会话私有临时目录 —— 即沙箱真正授权的那个
 *  --temp，也是 sandboxEnv 注入的 TMP/TEMP/TMPDIR；deny_* 是"拒绝的面"，收窄等于放宽，
 *  故仍解析为整个 tmpdir()。 */
function resolvePolicyPaths(
  packet: ProxyPolicyPacket | null | undefined,
  tempDir: string,
): ProxyPolicyPacket | null {
  if (!packet?.filesystem) return packet ?? null
  const allow = (xs?: string[]): string[] | undefined =>
    xs?.map((p) => (p === TMPDIR_MARK ? tempDir : p))
  const deny = (xs?: string[]): string[] | undefined =>
    xs?.map((p) => (p === TMPDIR_MARK ? tmpdir() : p))
  return {
    ...packet,
    filesystem: {
      ...packet.filesystem,
      allow_write: allow(packet.filesystem.allow_write),
      deny_write: deny(packet.filesystem.deny_write),
      deny_read: deny(packet.filesystem.deny_read),
    },
  }
}

type ProxyDecision = 'allow' | 'deny' | 'ask'

let server: Server | null = null
let port: number | null = null
let windowGetter: () => BrowserWindow | null = () => null

let currentPolicy: ProxyPolicyPacket | null = null
/** 会话级 host 缓存（已批准放行的 host），会话切换清空，不持久化 */
const sessionHostCache = new Map<string, { approved: boolean; ts: number }>()
/** 单次执行的 networkApprovals 缓冲，take 时取出清空 */
let networkApprovals: NetworkApproval[] = []
/** 进行中的 ask（同一 host 复用同一个弹窗结果） */
const inFlightAsks = new Map<string, Promise<boolean>>()
const pendingResolvers = new Map<string, (v: boolean) => void>()

// ===== 域名匹配 =====
// 无 glob → 后缀匹配（apex 含子域）；有 glob → glob（* / ?）；大小写不敏感。deny > allow 由调用方保证。
function isGlob(pattern: string): boolean {
  return /[*?\[\]]/.test(pattern)
}

export function matchDomain(host: string, pattern: string): boolean {
  const h = host.toLowerCase()
  const p = pattern.toLowerCase()
  if (!isGlob(p)) {
    return h === p || h.endsWith('.' + p)
  }
  const regex = new RegExp(
    '^' +
      p.replace(/[.+^${}()|[\]\\]/g, '\\$&').replace(/\*/g, '.*').replace(/\?/g, '.') +
      '$'
  )
  return regex.test(h)
}

// ===== 判定链 =====
function decideHost(host: string, protocol: 'http' | 'https'): ProxyDecision {
  const key = host.toLowerCase()
  // ① 无 policy / 未启用 → 直通（不记审计）
  if (!currentPolicy?.network?.enabled) return 'allow'
  const network = currentPolicy.network
  // ② 会话缓存（先前已批准）
  if (sessionHostCache.get(key)?.approved) return 'allow'
  // ③ network_rules 精确（host + protocol）
  const rule = network.network_rules?.find((r) => r.host === key && r.protocol === protocol)
  if (rule) {
    if (rule.decision === 'forbidden') return 'deny'
    if (rule.decision === 'prompt') return 'ask'
    return 'allow'
  }
  // ④ deny_domains（deny 优先于 allow）
  if (network.deny_domains?.some((p) => matchDomain(host, p))) return 'deny'
  // ⑤ allow_domains
  if (network.allow_domains?.some((p) => matchDomain(host, p))) return 'allow'
  // ⑥ approval_policy 总闸（never / granular 且 network:false → 拒）
  const ap = network.approval_policy
  if (ap === 'never') return 'deny'
  if (typeof ap === 'object' && ap !== null && (ap as Record<string, unknown>).network === false) return 'deny'
  // ⑦ unknown_domain（缺省按 ask，与服务端默认一致）
  if (network.unknown_domain === 'deny') return 'deny'
  return 'ask'
}

function recordApproval(
  host: string,
  protocol: string,
  decision: NetworkApproval['decision'],
  approved: boolean
): void {
  if (!currentPolicy?.network?.enabled) return
  // 同一 (host, protocol, 判定) 只记一条，避免 git clone 多连接刷屏审计
  if (networkApprovals.some((a) => a.host === host && a.protocol === protocol && a.decision === decision && a.approved === approved)) {
    return
  }
  networkApprovals.push({ host, protocol, decision, approved })
}

// ===== ask 弹窗（无客户端超时，等用户明确同意/拒绝）=====
// 注意：服务端等待客户端工具结果的窗口是 120s（query_loop._tool_wait_timeout），
// 弹窗停留超过该上限时服务端会先行超时。客户端侧不再自行兜底拒绝。
function askAndWait(host: string, protocol: string): Promise<boolean> {
  const key = `${host.toLowerCase()}:${protocol}`
  const existing = inFlightAsks.get(key)
  if (existing) return existing

  const promise = new Promise<boolean>((resolve) => {
    pendingResolvers.set(key, resolve)
    windowGetter()?.webContents.send('proxy:network-ask', { host, protocol })
  })
  inFlightAsks.set(key, promise)
  return promise
}

// ===== CONNECT / absolute-form =====
// req.url = "host:port" 或 "[::1]:443"；new URL 统一吃 IPv6 括号与绝对形式
function extractTarget(raw: string): { host: string; port: number } {
  const url = new URL('http://' + raw)
  return {
    host: url.hostname,
    port: url.port ? Number(url.port) : 443
  }
}

function sendForbidden(socket: { end: (chunk: string) => void }, message = 'HTTP/1.1 403 Forbidden\r\n\r\n'): void {
  socket.end(message)
}

function startProxy(): void {
  server = createServer()

  server.on('connect', (req, clientSocket, head) => {
    let target: { host: string; port: number }
    try {
      target = extractTarget(req.url || '')
    } catch {
      clientSocket.end('HTTP/1.1 400 Bad Request\r\n\r\n')
      return
    }
    const { host, port: targetPort } = target
    const protocol: 'https' = 'https'

    const finish = (approved: boolean): void => {
      recordApproval(host, protocol, approved ? 'allow' : 'deny', approved)
      if (!approved) {
        sendForbidden(clientSocket)
        return
      }
      const up = netConnect(targetPort, host, () => {
        clientSocket.write('HTTP/1.1 200 Connection Established\r\n\r\n')
        up.write(head)
        clientSocket.pipe(up)
        up.pipe(clientSocket)
      })
      up.on('error', () => clientSocket.end('HTTP/1.1 502 Bad Gateway\r\n\r\n'))
      clientSocket.on('error', () => up.destroy())
    }

    const decision = decideHost(host, protocol)
    if (decision === 'ask') {
      askAndWait(host, protocol).then(finish)
    } else {
      finish(decision !== 'deny')
    }
  })

  server.on('request', (req, res) => {
    let url: URL
    try {
      url = new URL(req.url || '')
    } catch {
      res.writeHead(400).end()
      return
    }
    const host = url.hostname
    const protocol: 'http' | 'https' = url.protocol === 'https:' ? 'https' : 'http'
    const targetPort = url.port ? Number(url.port) : protocol === 'https' ? 443 : 80

    const finish = (approved: boolean): void => {
      recordApproval(host, protocol, approved ? 'allow' : 'deny', approved)
      if (!approved) {
        res.writeHead(403).end('Forbidden by network policy')
        return
      }
      const headers = { ...req.headers, host: url.host }
      delete (headers as Record<string, unknown>).proxyConnection
      const upstream = (protocol === 'https' ? httpsRequest : httpRequest)(
        {
          host,
          port: targetPort,
          method: req.method,
          path: url.pathname + url.search,
          headers
        },
        (upRes) => {
          res.writeHead(upRes.statusCode || 502, upRes.headers)
          upRes.pipe(res)
        }
      )
      upstream.on('error', () => res.writeHead(502).end('Proxy error'))
      req.pipe(upstream)
    }

    const decision = decideHost(host, protocol)
    if (decision === 'ask') {
      askAndWait(host, protocol).then(finish)
    } else {
      finish(decision !== 'deny')
    }
  })

  server.on('error', () => {
    // 代理启动/运行异常不拖垮应用；getProxyEnv 端口未就绪时返回 {} → 子进程直连
  })

  server.listen(0, '127.0.0.1', () => {
    const addr = server?.address()
    if (addr && typeof addr === 'object') port = addr.port
  })
}

// ===== IPC（均走 ipcMain.handle invoke/handle 模式）=====
function registerIpcHandlers(): void {
  ipcMain.handle('proxy:set-policy', async (_event, packet: ProxyPolicyPacket | null | undefined) => {
    currentPolicy = resolvePolicyPaths(packet, await getSessionTempDir())
    // 沙箱侧消费：allow_write 里工作区/临时目录之外的根，由 sandbox 在 spawn 前补授 ACL
    setExtraWritableRoots(currentPolicy?.filesystem?.allow_write ?? [])
  })
  ipcMain.handle('proxy:get-port', () => port)
  ipcMain.handle('proxy:take-network-approvals', () => {
    const taken = networkApprovals
    networkApprovals = []
    return taken
  })
  ipcMain.handle('proxy:reset-session', () => {
    sessionHostCache.clear()
  })
  ipcMain.handle('proxy:resolve-network', (_event, host: string, protocol: string, approved: boolean) => {
    const key = `${String(host).toLowerCase()}:${String(protocol)}`
    const resolve = pendingResolvers.get(key)
    if (!resolve) return
    pendingResolvers.delete(key)
    inFlightAsks.delete(key)
    if (approved) {
      sessionHostCache.set(key.split(':')[0], { approved: true, ts: Date.now() })
    }
    resolve(Boolean(approved))
  })
}

export function registerProxyIpc(getWindow: () => BrowserWindow | null): void {
  windowGetter = getWindow
  registerIpcHandlers()
  startProxy()
}

/** 当前策略包整包（沙箱读 sandbox.required） */
export function getCurrentPolicy(): ProxyPolicyPacket | null {
  return currentPolicy
}

/** 供 bash/MCP spawn 注入的代理 env；端口未就绪返回 {}（直连） */
export function getProxyEnv(): Record<string, string> {
  const proxy = getProxyUrl()
  if (!proxy) return {}
  return {
    HTTPS_PROXY: proxy,
    HTTP_PROXY: proxy,
    ALL_PROXY: proxy,
    NO_PROXY: 'localhost,127.0.0.1'
  }
}

/** 本地代理地址；端口未就绪返回 null（主进程 fetch 走不了代理时由调用方决定回退/告警） */
export function getProxyUrl(): string | null {
  return port === null ? null : `http://127.0.0.1:${port}`
}
