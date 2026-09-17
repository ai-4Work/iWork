import { ipcMain, dialog } from 'electron'
import { readFile, writeFile, readdir } from 'fs/promises'
import os from 'os'
import AdmZip from 'adm-zip'
import { join, resolve, dirname, basename } from 'path'
import { exec as cpExec, execFile as cpExecFile } from 'child_process'
import { getProxyEnv, getCurrentPolicy } from './proxy'
import { doctor, runSandboxed } from './sandbox'
import { resolvePowerShell, resolveShell } from './shell'

interface BufferExecResult {
  stdout: Buffer
  stderr: Buffer
}

function execFileBuffer(
  file: string,
  args: string[],
  options: {
    cwd?: string
    timeout?: number
    maxBuffer?: number
    env?: NodeJS.ProcessEnv
    windowsHide?: boolean
  }
): Promise<BufferExecResult> {
  return new Promise((resolve, reject) => {
    cpExecFile(file, args, { ...options, encoding: 'buffer' }, (error, stdout, stderr) => {
      const result = {
        stdout: Buffer.isBuffer(stdout) ? stdout : Buffer.from(stdout || ''),
        stderr: Buffer.isBuffer(stderr) ? stderr : Buffer.from(stderr || '')
      }

      if (error) {
        Object.assign(error, result)
        reject(error)
        return
      }

      resolve(result)
    })
  })
}

function execBuffer(
  command: string,
  options: {
    cwd?: string
    timeout?: number
    maxBuffer?: number
    shell?: string
    env?: NodeJS.ProcessEnv
  }
): Promise<BufferExecResult> {
  return new Promise((resolve, reject) => {
    cpExec(command, { ...options, encoding: 'buffer' }, (error, stdout, stderr) => {
      const result = {
        stdout: Buffer.isBuffer(stdout) ? stdout : Buffer.from(stdout || ''),
        stderr: Buffer.isBuffer(stderr) ? stderr : Buffer.from(stderr || '')
      }

      if (error) {
        Object.assign(error, result)
        reject(error)
        return
      }

      resolve(result)
    })
  })
}

// 会话内 cd 持久：首次为空 → 用工作区根目录；成功执行 cd 后记录绝对路径
let sessionCwd: string | null = null

function resolveCwd(ws: string): string {
  return sessionCwd ? sessionCwd : resolve(ws)
}

// PowerShell 非交互输出走管道时为 OEM/GBK 编码；前置 UTF-8 设置让输出编码一致可读。
// 仅裸机分支使用（沙箱有 PS_OUTPUT_UTF8，见 sandbox.ts buildRunnerArgv；两处都别重复拼）。
// 包 try/catch：沙箱的受限令牌会让 PS 进 ConstrainedLanguage，对 [Console] 这类非核心类型
// 设属性被禁并抛 PropertySetterNotSupportedInConstrainedLanguage（非终止错误 → 污染 stderr）。
const PS_UTF8_PREFIX = 'try { $OutputEncoding = [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch {}; '

// 提取命令开头的 cd 目标（PowerShell：cd / Set-Location / sl，可选 -Path 前缀），非 cd 命令返回 null
function extractCdTarget(command: string, currentCwd: string): string | null {
  const cdOnly = /^\s*(?:cd|Set-Location|sl)\s*$/i.exec(command)
  if (cdOnly) return os.homedir()
  const m = /^\s*(?:cd|Set-Location|sl)(?:\s+-Path)?\s+(\S+)/i.exec(command)
  if (!m) return null
  const target = m[1].replace(/^['"]|['"]$/g, '')
  if (target === '~') return os.homedir()
  return resolve(currentCwd, target)
}

// 会话执行环境：powershell | zsh | bash（服务端据此注入 Shell: ... 提示词）
export function detectShellEnv(): string {
  if (process.platform === 'win32') return 'powershell'
  if (process.platform === 'darwin') {
    return (process.env.SHELL || '/bin/zsh').endsWith('zsh') ? 'zsh' : 'bash'
  }
  return 'bash'
}

function decodeBuffer(buf: Buffer | string | undefined): string {
  if (!buf) return ''
  if (typeof buf === 'string') return buf
  if (buf.length === 0) return ''
  if (process.platform !== 'win32') return buf.toString('utf8')

  // Try UTF-8 first (strict — invalid bytes → replacement chars).
  // Fall back to GBK if UTF-8 produces too many replacement chars.
  const utf8 = new TextDecoder('utf-8', { fatal: false }).decode(buf)
  const utf8Bad = (utf8.match(/�/g) || []).length
  if (utf8Bad < utf8.length * 0.05) return utf8

  // Likely GBK (cmd.exe or Windows-native tool output)
  try {
    const gbk = new TextDecoder('gbk', { fatal: false }).decode(buf)
    const gbkBad = (gbk.match(/�/g) || []).length
    if (gbkBad < gbk.length * 0.05) return gbk
  } catch { /* TextDecoder('gbk') not available */ }

  return utf8
}

// 子进程被超时终止时既无退出码也无 stderr，渲染端只按 `exit_code!==0 && stderr` 判失败，
// 会把「超时」读成「成功且无输出」。显式补一条超时信息走 stderr，让它以失败形态回传。
function timeoutMarker(timeoutMs: number, stderr: string): string {
  const head = `[命令超时] 超过 ${timeoutMs}ms 未返回，已终止。`
  return stderr ? `${head}\n${stderr}` : head
}

// 沙箱豁免：命令首个 token 的 basename（去引号、去 .exe、小写）命中服务端下发名单 → 裸机执行。
// 只识别单条命令，不解析复合语句（`a; bsk` 之类不豁免）。
function isSandboxExempt(command: string, list?: string[]): boolean {
  if (!list || list.length === 0) return false
  const first = command.trim().replace(/^&\s*/, '').match(/^("[^"]+"|'[^']+'|\S+)/)?.[0]
  if (!first) return false
  const base = basename(first.replace(/^["']|["']$/g, '')).replace(/\.exe$/i, '').toLowerCase()
  return list.some((x) => String(x).trim().toLowerCase() === base)
}

// 读 Windows 注册表持久化环境变量（HKCU 覆盖 HKLM），展开 REG_EXPAND_SZ 的 %VAR%。
// 跳过 Path：用注册表 Path 覆盖 process.env.PATH 会丢掉父进程追加的路径（登录 shell 已正确设置 PATH）。
// 解决 setx 后应用 process.env 仍是启动时快照、新变量读不到的问题（每次 exec 重读，无需重启应用）。
async function readWindowsRegistryEnv(): Promise<Record<string, string>> {
  const env: Record<string, string> = {}
  const keys = [
    'HKLM\\SYSTEM\\CurrentControlSet\\Control\\Session Manager\\Environment',
    'HKCU\\Environment'
  ]
  for (const key of keys) {
    try {
      const { stdout } = await execFileBuffer('reg.exe', ['query', key], { windowsHide: true })
      for (const line of decodeBuffer(stdout).split(/\r?\n/)) {
        const m = /^\s*(.+?)\s+(REG_[A-Z_]+)(?:\s+(.*))?$/.exec(line)
        if (!m) continue
        const [, name, type, value = ''] = m
        if (/^PATH$/i.test(name)) continue
        if (type !== 'REG_SZ' && type !== 'REG_EXPAND_SZ') continue
        env[name] = value
      }
    } catch {
      // 注册表键不存在/无权限 → 跳过
    }
  }
  const base = { ...process.env, ...env }
  for (const [k, v] of Object.entries(env)) {
    if (/%[^%]+%/.test(v)) {
      env[k] = v.replace(/%([^%]+)%/g, (_m, name) => base[name] ?? '')
    }
  }
  return env
}

// 读注册表持久化 PATH（HKLM + HKCU，HKCU 追加在后）。git-bash 登录 shell 会自动补 PATH，
// 换 PowerShell（非登录）后必须显式合并，否则 app 继承的 PATH 里没有 git/node 时命令找不到。
async function readWindowsRegistryPath(): Promise<string> {
  const parts: string[] = []
  const keys = [
    'HKLM\\SYSTEM\\CurrentControlSet\\Control\\Session Manager\\Environment',
    'HKCU\\Environment'
  ]
  for (const key of keys) {
    try {
      const { stdout } = await execFileBuffer('reg.exe', ['query', key], { windowsHide: true })
      for (const line of decodeBuffer(stdout).split(/\r?\n/)) {
        const m = /^\s*PATH\s+REG_[A-Z_]+(?:\s+(.*))?$/i.exec(line)
        if (m && m[1]) parts.push(m[1])
      }
    } catch {
      // 注册表键不存在/无权限 → 跳过
    }
  }
  return parts.join(';')
}

function globToRegex(pattern: string): RegExp {
  const escaped = pattern
    .replace(/[.+^${}()|[\]\\]/g, '\\$&')
    .replace(/\*\*\//g, '\x00')
    .replace(/\*/g, '[^/\\\\]*')
    .replace(/\x00/g, '(.*/)?')
  return new RegExp(`^${escaped}$`)
}

async function globFiles(basePath: string, pattern: string): Promise<string[]> {
  const results: string[] = []
  const regex = globToRegex(pattern)

  async function walk(dir: string) {
    try {
      const entries = await readdir(dir, { withFileTypes: true })
      for (const entry of entries) {
        const fullPath = join(dir, entry.name)
        const relativePath = fullPath.replace(basePath, '').replace(/^[/\\]/, '')
        if (entry.isDirectory()) {
          if (entry.name === 'node_modules' || entry.name === '.git') continue
          await walk(fullPath)
        } else if (entry.isFile()) {
          if (regex.test(relativePath)) {
            results.push(relativePath)
          }
        }
      }
    } catch {
      // skip inaccessible directories
    }
  }

  await walk(basePath)
  return results
}

function resolvePath(workspaceRoot: string, targetPath: string): string {
  return resolve(workspaceRoot, targetPath)
}

export function registerFileOps(workspacePath: () => string): void {
  const ws = () => {
    const p = workspacePath()
    if (!p) throw new Error('No workspace selected')
    return p
  }

  ipcMain.handle('file:glob', async (_event, pattern: string) => {
    return globFiles(ws(), pattern)
  })

  ipcMain.handle('file:read', async (_event, filePath: string) => {
    return readFile(resolvePath(ws(), filePath), 'utf-8')
  })

  ipcMain.handle('file:write', async (_event, filePath: string, content: string) => {
    const fullPath = resolvePath(ws(), filePath)
    await (await import('fs/promises')).mkdir(dirname(fullPath), { recursive: true })
    return writeFile(fullPath, content, 'utf-8')
  })

  ipcMain.handle('file:edit', async (_event, filePath: string, oldStr: string, newStr: string) => {
    const fullPath = resolvePath(ws(), filePath)
    const content = await readFile(fullPath, 'utf-8')
    if (!content.includes(oldStr)) throw new Error('old_string not found in file')
    return writeFile(fullPath, content.replace(oldStr, newStr), 'utf-8')
  })

  ipcMain.handle('file:grep', async (_event, pattern: string, dirPath: string) => {
    const base = ws()
    const searchDir = resolvePath(base, dirPath || '.')
    const results: string[] = []
    const regex = new RegExp(pattern, 'g')

    async function search(dir: string) {
      const entries = await readdir(dir, { withFileTypes: true })
      for (const entry of entries) {
        const fullPath = join(dir, entry.name)
        if (entry.isDirectory()) {
          if (entry.name === 'node_modules' || entry.name === '.git') continue
          await search(fullPath)
        } else if (entry.isFile()) {
          try {
            const content = await readFile(fullPath, 'utf-8')
            const lines = content.split('\n')
            const relativePath = fullPath.replace(base, '').replace(/^[/\\]/, '')
            lines.forEach((line: string, i: number) => {
              if (regex.test(line)) {
                results.push(`${relativePath}:${i + 1}: ${line.trim()}`)
              }
            })
          } catch {
            // skip binary files
          }
        }
      }
    }

    await search(searchDir)
    return results
  })

  ipcMain.handle('shell:env', async () => detectShellEnv())

  ipcMain.handle('file:exec', async (_event, command: string, timeoutMs: number = 300000) => {
    const cwd = resolveCwd(resolve(ws()))
    const timeout = Math.min(timeoutMs, 300000)
    const maxBuffer = 10 * 1024 * 1024
    const registryEnv = process.platform === 'win32' ? await readWindowsRegistryEnv() : {}
    const env: NodeJS.ProcessEnv = {
      ...process.env,
      ...registryEnv,
      ...getProxyEnv(),
      HOME: cwd,
      // USERPROFILE 必须是真实家目录：Node/Python/PowerShell 都只认它来解析 `~`
      // （Windows 上优先 USERPROFILE，忽略 HOME）。改成工作区会让技能脚本读不到
      // 用户级配置（~/.config/*、~/.gitconfig…）。往 `~` 写的限制由沙箱 ACL 承担。
      // HOME 保留指工作区：MSYS/git-bash 类工具优先读 HOME，让它们仍写在工作区内。
      USERPROFILE: os.homedir(),
      LANG: 'zh_CN.UTF-8',
      LC_ALL: 'zh_CN.UTF-8'
    }
    if (process.platform === 'win32') {
      const regPath = await readWindowsRegistryPath()
      if (regPath) env.PATH = (process.env.PATH ? process.env.PATH + ';' : '') + regPath
    }
    // Windows 统一走 PowerShell（裸机/沙箱同语法契约）。UTF-8 前缀由各分支自己拼一次：
    // 沙箱在 buildRunnerArgv 拼 PS_OUTPUT_UTF8，裸机在下面拼 PS_UTF8_PREFIX，不要在这里重复加。
    // cd 持久化需用原始命令解析。
    const rawCommand = command

    // 策略要求沙箱 → 命令类工具走 dsh-sandbox-windows-acl（写受限，fail-closed）。
    const policy = getCurrentPolicy()
    // 服务端下发的豁免命令（如 bsk：必须访问自己的命名管道/家目录，写受限令牌下必然 EPERM）
    // 走裸机执行路径，其余仍受沙箱约束。
    const sandboxRequired = policy?.sandbox?.required === true
    const exempt = sandboxRequired && isSandboxExempt(rawCommand, policy?.sandbox?.exempt_commands)
    // 本次执行实际运行在沙箱/裸机（渲染端据此在命令卡片上标注“沙箱运行/裸机运行”）
    const sandboxed = sandboxRequired && !exempt
    if (sandboxRequired && !exempt) {
      // 探活/启动失败时命令并未真正在沙箱内执行 → 如实返回 sandboxed:false（不谎报“沙箱运行”）
      if (!(await doctor())) {
        return { stdout: '', stderr: '沙箱不可用：dsh-sandbox-windows-acl 未就绪', exit_code: 1, sandboxed: false }
      }
      try {
        const res = await runSandboxed(command, cwd, env, timeout)
        const stderr = decodeBuffer(res.stderr)
        return {
          stdout: decodeBuffer(res.stdout),
          stderr: res.timedOut ? timeoutMarker(timeout, stderr) : stderr,
          exit_code: res.exit_code,
          sandboxed
        }
      } catch (err: any) {
        return { stdout: '', stderr: `沙箱执行失败：${err?.message || String(err)}`, exit_code: 1, sandboxed: false }
      }
    }

    try {
      let stdout: Buffer
      let stderr: Buffer

      if (process.platform === 'win32') {
        // execFile 直接传 argv，不经中间 shell，避免引号/转义问题；PowerShell 非登录不读 profile。
        const result = await execFileBuffer(resolvePowerShell(), ['-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-Command', PS_UTF8_PREFIX + command], {
          cwd,
          timeout,
          maxBuffer,
          env,
          windowsHide: true
        })
        stdout = result.stdout
        stderr = result.stderr
      } else {
        const result = await execBuffer(command, {
          cwd,
          timeout,
          maxBuffer,
          shell: resolveShell(),
          env
        })
        stdout = result.stdout
        stderr = result.stderr
      }

      const result = {
        stdout: decodeBuffer(stdout),
        stderr: decodeBuffer(stderr),
        exit_code: 0,
        sandboxed
      }
      // cd 持久：仅命令成功（exit_code 0）时更新会话目录
      const cdTarget = extractCdTarget(rawCommand, cwd)
      if (cdTarget) sessionCwd = cdTarget
      return result
    } catch (err: any) {
      // Command failed — decode stderr/out buffers properly instead of
      // letting Node's raw error message propagate through IPC.
      // 超时被 kill 时 err.code 为 null（signal 终止，无退出码）且 stderr 为空。
      const stderr = decodeBuffer(err.stderr)
      const timedOut = err?.killed === true && err?.code == null
      return {
        stdout: decodeBuffer(err.stdout),
        stderr: timedOut ? timeoutMarker(timeout, stderr) : stderr,
        exit_code: err.code ?? 1,
        sandboxed
      }
    }
  })

  ipcMain.handle('file:extractSkill', async (_event, base64Content: string, skillName: string): Promise<string> => {
    const fs = await import('fs/promises')
    const path = await import('path')
    const os = await import('os')

    // Resolve target directory: ~/.iwork/skills/{skillName}/
    const homeDir = os.homedir()
    const targetDir = path.join(homeDir, '.iwork', 'skills', skillName)

    // Ensure parent directories exist
    await fs.mkdir(targetDir, { recursive: true })

    // Decode base64 → binary zip
    const zipBuffer = Buffer.from(base64Content, 'base64')

    // Extract using adm-zip (pure JS, cross-platform)
    try {
      const zip = new AdmZip(zipBuffer)
      zip.extractAllTo(targetDir, true)
    } catch (err: any) {
      try { await fs.mkdir(targetDir, { recursive: true }) } catch {}
      throw new Error(`Failed to extract zip: ${err.message}`)
    }
    return targetDir
  })

  ipcMain.handle('file:extractPlugin', async (_event, base64Content: string, zipName: string): Promise<string> => {
    const fs = await import('fs/promises')
    const path = await import('path')
    const os = await import('os')

    // Resolve target directory: ~/.iwork/plugin/{zipName}/
    const homeDir = os.homedir()
    const targetDir = path.join(homeDir, '.iwork', 'plugin', zipName)

    await fs.mkdir(targetDir, { recursive: true })


    // Decode base64 → binary zip
    const zipBuffer = Buffer.from(base64Content, 'base64')

    // Extract using adm-zip (pure JS, cross-platform)
    try {
      const zip = new AdmZip(zipBuffer)
      zip.extractAllTo(targetDir, true)
    } catch (err: any) {
      try { await fs.mkdir(targetDir, { recursive: true }) } catch {}
      throw new Error(`Failed to extract zip: ${err.message}`)
    }
    return targetDir
  })

  ipcMain.handle('file:readSkillMd', async (_event, skillId: string): Promise<string> => {
    const os = await import('os')
    const path = await import('path')
    const homeDir = os.homedir()
    const mdPath = path.join(homeDir, '.iwork', 'skills', skillId, 'SKILL.md')
    return readFile(mdPath, 'utf-8')
  })

  ipcMain.handle('file:getSkillDir', async (_event, skillId: string): Promise<string> => {
    const os = await import('os')
    const path = await import('path')
    const homeDir = os.homedir()
    return path.join(homeDir, '.iwork', 'skills', skillId).replace(/\\/g, '/')
  })

  ipcMain.handle('workspace:select', async () => {
    const result = await dialog.showOpenDialog({
      properties: ['openDirectory']
    })
    if (result.canceled || result.filePaths.length === 0) return null
    return result.filePaths[0]
  })
}
