// ===== 沙箱：@deepseek-ai/dsh-sandbox-windows-acl runner（Windows 写受限）=====
// 机制：WRITE_RESTRICTED 受限令牌 + 按工作区路径派生的写 SID（S-1-4-…）白名单，
// 只限制写；读、网络、进程可见性不受限（网络由 proxy.ts 覆盖）。fail-closed：
// 任何 Win32 失败 → runner stderr 打 `windows-acl-run: <detail>`、exit 127，
// 子进程绝不无限制运行。包为 ESM-only，主进程（CJS）不直接 import —— 走包内
// runner.js 子路径，用 ELECTRON_RUN_AS_NODE=1 让 process.execPath（electron.exe）
// 以纯 Node 模式执行它。模式固定 workspace-write（工作区根可写）。
// 受限进程用 Windows PowerShell：WRITE_RESTRICTED 令牌与 MSYS/bash、cmd.exe 实测不兼容
// （MSYS 在 DLL 初始化建共享内存映射被拒、cmd 忽略 /c 进交互），原生 exe（PS/node）正常。
import { createRequire } from 'module'
import { spawn } from 'child_process'
import { existsSync, statSync } from 'fs'
import { mkdir, mkdtemp, writeFile } from 'fs/promises'
import { tmpdir } from 'os'
import { join, dirname, resolve, delimiter } from 'path'
import { resolvePowerShell, PS_OUTPUT_UTF8 } from './shell'

const RUNNER_FAILURE_SIGNATURE = 'windows-acl-run:'
const RUNNER_FAILURE_EXIT = 127
const DOCTOR_TTL_MS = 2000

const nodeRequire = createRequire(__filename)
let runnerPath: string | null = null

/** 包内 runner 入口 `lib/runner.js` 的绝对路径（从 package.json 定位包根） */
function runnerCli(): string {
  if (!runnerPath) {
    const pkgJson = nodeRequire.resolve('@deepseek-ai/dsh-sandbox-windows-acl/package.json')
    runnerPath = join(dirname(pkgJson), 'lib', 'runner.js')
  }
  return runnerPath
}

// ===== 隐藏控制台 shim：解决沙箱路径执行 PowerShell 弹黑窗 =====
// runner（electron.exe，GUI 子系统、无控制台）用 CreateProcessAsUserW 派生 PowerShell
// （控制台子系统）时 dwCreationFlags=0（CREATE_NO_WINDOW 会让受限令牌子进程 0xC0000142
// 崩溃，包作者有意不加），父无控制台 → Windows 给子进程新建可见控制台窗口（黑窗）。
// 方案：先让 runner 进程自己 AllocConsole + ShowWindow(SW_HIDE) 拥有一个隐藏控制台，
// 再 import 真正的 runner.js；PowerShell 随即继承该隐藏控制台而非新建窗口。
// shim 是被 electron 以 ELECTRON_RUN_AS_NODE 直接执行的入口模块（.mjs，ESM），
// koffi（FFI 调 kernel32/user32）与 runner 的绝对路径都从 env 传入（shim 在临时目录，
// 无法自行解析 node_modules）。koffi 未被 electron-vite 外置打包，故不能 import 到主进程
// bundle，只能作为独立运行时文件 + createRequire(绝对路径) 加载。
const SHIM_SOURCE = `import { pathToFileURL } from 'node:url'
import { createRequire } from 'node:module'

const require = createRequire(import.meta.url)
const koffi = require(process.env.DSH_KOFFI_PATH)

const kernel32 = koffi.load('kernel32.dll')
const user32 = koffi.load('user32.dll')
const AllocConsole = kernel32.func('AllocConsole', 'bool', [])
const GetConsoleWindow = kernel32.func('GetConsoleWindow', 'void*', [])
const ShowWindow = user32.func('ShowWindow', 'bool', ['void*', 'int'])
const SW_HIDE = 0
// 隐藏控制台活动代码页设 65001(UTF-8)：PowerShell 挂靠控制台时，对象表格化
// （Format-Table/Get-ChildItem 目录头）按“活动输出代码页”生成文本；不设则用 936(GBK)，
// 表格里的中文成 GBK 字节，与管道侧 [Console]::OutputEncoding=UTF8 的字符串输出分裂。
const SetConsoleOutputCP = kernel32.func('SetConsoleOutputCP', 'bool', ['uint'])
const SetConsoleCP = kernel32.func('SetConsoleCP', 'bool', ['uint'])
const CP_UTF8 = 65001

try {
  AllocConsole()
  SetConsoleOutputCP(CP_UTF8)
  SetConsoleCP(CP_UTF8)
  const hwnd = GetConsoleWindow()
  if (hwnd) ShowWindow(hwnd, SW_HIDE)
} catch {}

await import(pathToFileURL(process.env.DSH_RUNNER_PATH).href)
`

let koffiPath: string | null = null
function koffiCli(): string {
  if (!koffiPath) koffiPath = nodeRequire.resolve('koffi')
  return koffiPath
}

let shimPath: string | null = null
/** 写一份 shim 到会话临时目录（缓存，每次会话只写一次），返回其绝对路径 */
async function getShimPath(): Promise<string> {
  if (!shimPath) {
    const dir = await getSessionTempDir()
    shimPath = join(dir, 'console-shim.mjs')
    await writeFile(shimPath, SHIM_SOURCE, 'utf-8')
  }
  return shimPath
}

// ===== 会话私有临时目录：既是 runner 的 --temp，也是子进程 TMP/TEMP/TMPDIR 的指向 =====
// （bwrap --tmpfs /tmp 语义；共享一个目录，runner 每次调用重新授权，精确 ACE 跳过）
// 环境变量由本模块的 sandboxEnv 注入 —— runner 只在传了 --write-sid 时才自己改写（runner.js:124-128），本 seam 不传。
let sessionTempDir: string | null = null
export async function getSessionTempDir(): Promise<string> {
  if (!sessionTempDir) sessionTempDir = await mkdtemp(join(tmpdir(), 'agent-dsh-'))
  return sessionTempDir
}

// ===== 服务端 allow_write 里工作区/临时目录之外的额外可写根 =====
// 受限令牌的写检查是 SID 交集：令牌 restricting 列表里只有 runner 派生的那一个写 SID
// （runner.js:116），所以任何目录只要挂上该 SID 的写 ACE 就在沙箱内可写 —— 不必让 runner
// 知道它（runner.js:118 把可写根写死成 [--workspace] 一个）。
// 授权只能走包内的 AclWriteGrant（原始 SetEntriesInAclW/SetNamedSecurityInfoW）：icacls
// 对孤儿 SID 会 ERROR_NONE_MAPPED(1332)（坚持做名字↔SID 映射，实测），而包 index.js 又
// 进不了主进程 bundle（types-Br60qOzH.js:4 顶层 import koffi，koffi 是传递依赖未外置）。
// 故照 console-shim 的路子另起一个纯 Node 进程跑包代码 —— 与 runner 用同一个 SID 派生函数。
const GRANT_HELPER_SOURCE = `import { pathToFileURL } from 'node:url'
const { AclWriteGrant, workspaceWriteSid } = await import(pathToFileURL(process.env.DSH_SANDBOX_INDEX).href)
const [workspace, ...dirs] = process.argv.slice(2)
const grant = AclWriteGrant.create(workspaceWriteSid(workspace))
for (const dir of dirs) grant.add(dir, true)
`

let grantHelperPath: string | null = null
async function getGrantHelperPath(): Promise<string> {
  if (!grantHelperPath) {
    const dir = await getSessionTempDir()
    grantHelperPath = join(dir, 'grant-ace.mjs')
    await writeFile(grantHelperPath, GRANT_HELPER_SOURCE, 'utf-8')
  }
  return grantHelperPath
}

let sandboxIndexPath: string | null = null
function grantHelperIndex(): string {
  if (!sandboxIndexPath) sandboxIndexPath = nodeRequire.resolve('@deepseek-ai/dsh-sandbox-windows-acl')
  return sandboxIndexPath
}

let extraWritableRoots: string[] = []
/** 已授权过的 (workspace, dir)：ACE 是 standing 的，同一进程内不重复跑 helper */
const grantedRoots = new Set<string>()

/** 由 proxy.ts 在收到策略包（proxy:set-policy）时灌入，取自 filesystem.allow_write */
export function setExtraWritableRoots(dirs: string[]): void {
  extraWritableRoots = dirs
}

/** 沙箱内可写 = 令牌携带的写 SID 有 ACE 的目录。workspace/temp 由 runner 每次授权，这里只补授
 *  配置里的额外根；失败只影响该根（降级为不可写），不抛 —— 别把整个沙箱拖成不可用。 */
async function ensureExtraWritableGrants(workspace: string, tempDir: string): Promise<void> {
  const workspaceKey = resolve(workspace)
  const pending = extraWritableRoots
    .map((raw) => (raw ? resolve(raw) : ''))
    .filter((dir) => dir && dir !== workspaceKey && dir !== resolve(tempDir))
    .filter((dir) => !grantedRoots.has(`${workspaceKey}|${dir}`))
    // fail-closed：不存在的目录必须丢，否则包内 grant 抛错（runner 同款契约）
    .filter((dir) => existsSync(dir) && statSync(dir).isDirectory())
  if (pending.length === 0) return
  if (await runGrantHelper(workspace, pending)) {
    for (const dir of pending) grantedRoots.add(`${workspaceKey}|${dir}`)
  }
}

/** 以 workspace 字面量为 SID 入参（与 runner 收到的 --workspace 同源同字面，runner 不规范化），
 *  故 helper 派生的 SID 与 runner 完全一致；ACE 以 standing 添加（跨会话复用缓存，不撤销）。 */
async function runGrantHelper(workspace: string, dirs: string[]): Promise<boolean> {
  try {
    const { stderr, code } = await runToBuffer(
      process.execPath,
      [await getGrantHelperPath(), workspace, ...dirs],
      {
        cwd: workspace,
        env: { ...process.env, ELECTRON_RUN_AS_NODE: '1', DSH_SANDBOX_INDEX: grantHelperIndex() },
        timeout: 180000
      }
    )
    if (code !== 0) {
      console.warn(`[sandbox] 授权额外可写根失败(${code}): ${stderr.toString('utf8').trim().slice(0, 300)}`)
      return false
    }
    return true
  } catch (err) {
    console.warn(`[sandbox] 授权额外可写根异常: ${(err as Error)?.message}`)
    return false
  }
}

/** runner argv：受限进程 = PowerShell。沙箱化命令必须为 PowerShell 语法（服务端在
 * sandbox.required 时下发 PS 命令）；-NoProfile 避免读/写 profile，-NonInteractive 不弹交互。
 * 入口是 console-shim（先隐藏控制台再 import runner），argv 契约由 runner 原样解析。 */
async function buildRunnerArgv(command: string, workspace: string, tempDir: string): Promise<string[]> {
  const ps = resolvePowerShell()
  return [
    await getShimPath(),
    '--workspace', workspace,
    '--temp', tempDir,
    '--mode', 'workspace-write',
    '--', ps, '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-Command', PS_OUTPUT_UTF8 + command
  ]
}

// ===== 沙箱内 Python 的建目录 shim（PYTHONPATH 上的 sitecustomize）=====
// Windows 上 os.mkdir(path, mode) 在 mode 不含 group/other 位时会显式写一个 owner-only
// DACL（SYSTEM/Administrators/OWNER RIGHTS）而不继承父目录 ACE —— 该目录里既没有 logon SID
// 也没有写 SID，受限令牌的 pass-2（restricting SID 交集）全拒，于是"建得出来、建完碰不到"：
// pip 的 pip-install-/pip-build-tracker-（tempfile.mkdtemp 固定 0o700）在沙箱内直接
// Errno 13 失败，pytest tmp_path、TemporaryDirectory、setuptools 构建同理。
// 补一个 group 位（0o700→0o770）就回到正常继承路径，把写 SID 带回来。文件不受影响
// （os.open 各 mode 都继承），故只包 mkdir。
// 只在沙箱 env 里挂 PYTHONPATH，不碰用户环境；目录独立于会话 temp 根 —— 把 temp 根塞进
// sys.path 前置位会让临时垃圾里的同名文件遮蔽标准库。
const PY_SHIM_SOURCE = `import os

_orig_mkdir = os.mkdir


def _mkdir(path, mode=0o777, *args, **kwargs):
    if isinstance(mode, int) and not mode & 0o077:
        mode |= 0o070
    return _orig_mkdir(path, mode, *args, **kwargs)


os.mkdir = _mkdir
`

let pyShimDir: string | null = null
async function getPyShimDir(): Promise<string> {
  if (!pyShimDir) {
    pyShimDir = join(await getSessionTempDir(), 'pyshim')
    await mkdir(pyShimDir, { recursive: true })
    await writeFile(join(pyShimDir, 'sitecustomize.py'), PY_SHIM_SOURCE, 'utf-8')
  }
  return pyShimDir
}

/** 沙箱 spawn 额外 env：shim 需要 koffi 与 runner 的绝对路径（临时目录内无法自行解析）。
 *  TMP/TEMP/TMPDIR 必须显式指向会话私有 --temp —— runner 只在传了 --write-sid 时才改写
 *  TMP/TEMP（runner.js:124-128），本 seam 不传该标志，子进程原样继承这里的值；不设就继承
 *  Electron 的 %TEMP%（C 盘，不在受限令牌白名单）→ Python tempfile 候选链全败 →
 *  兜底 os.getcwd()，临时物落进工作区。
 *  PYTHONPATH 前置 shim 目录（见 getPyShimDir），修 mkdir 的 owner-only DACL。 */
async function sandboxEnv(base: NodeJS.ProcessEnv, tempDir: string): Promise<NodeJS.ProcessEnv> {
  const pyShim = await getPyShimDir()
  return {
    ...base,
    TMP: tempDir,
    TEMP: tempDir,
    TMPDIR: tempDir,
    ELECTRON_RUN_AS_NODE: '1',
    DSH_KOFFI_PATH: koffiCli(),
    DSH_RUNNER_PATH: runnerCli(),
    PYTHONPATH: base.PYTHONPATH ? pyShim + delimiter + base.PYTHONPATH : pyShim
  }
}

// ===== doctor：探活 + 短 TTL 缓存。固定探活目录 → 派生 SID/ACE 稳定复用，不累积垃圾 =====
let doctorCache: { ok: boolean; ts: number } | null = null
const DOCTOR_DIR = join(tmpdir(), 'agent-dsh-doctor')

export async function doctor(): Promise<boolean> {
  const now = Date.now()
  if (doctorCache && now - doctorCache.ts < DOCTOR_TTL_MS) return doctorCache.ok
  let ok = false
  try {
    const tempDir = await getSessionTempDir()
    await mkdir(DOCTOR_DIR, { recursive: true })
    // `exit 0` 在 PowerShell 下返回 0；sandbox 不可用时 runner 以 127 + 签名退出
    const argv = await buildRunnerArgv('exit 0', DOCTOR_DIR, tempDir)
    const { stderr, code } = await runToBuffer(process.execPath, argv, {
      cwd: DOCTOR_DIR,
      env: await sandboxEnv(process.env, tempDir),
      timeout: 15000
    })
    ok = code === 0 && !stderr.toString('utf8').startsWith(RUNNER_FAILURE_SIGNATURE)
  } catch {
    ok = false
  }
  doctorCache = { ok, ts: now }
  return ok
}

// ===== runSandboxed：runner 包住 PowerShell -Command <command>，stdio 管道捕获 =====
export interface SandboxedResult {
  stdout: Buffer
  stderr: Buffer
  exit_code: number
  /** 命中超时被 kill（无退出码）——调用方据此补超时信息，否则会被上层误读成普通失败或成功 */
  timedOut: boolean
}

export async function runSandboxed(
  command: string,
  cwd: string,
  env: NodeJS.ProcessEnv,
  timeoutMs: number
): Promise<SandboxedResult> {
  const tempDir = await getSessionTempDir()
  await ensureExtraWritableGrants(cwd, tempDir)
  const argv = await buildRunnerArgv(command, cwd, tempDir)
  const { stdout, stderr, code, timedOut } = await runToBuffer(process.execPath, argv, {
    cwd,
    env: await sandboxEnv(env, tempDir),
    timeout: timeoutMs
  })
  if (code === RUNNER_FAILURE_EXIT && stderr.toString('utf8').startsWith(RUNNER_FAILURE_SIGNATURE)) {
    // runner 侧失败（token/授权/spawn 出错）——区分于普通命令失败，抛给调用方报“沙箱执行失败”
    throw new Error(stderr.toString('utf8').split('\n')[0].trim())
  }
  return { stdout, stderr, exit_code: code, timedOut }
}

// ===== 沙箱化任意常驻子进程（MCP stdio server）的 spawn 参数 =====
// 与 runSandboxed 同机制：console-shim（隐藏控制台）→ runner → WRITE_RESTRICTED 令牌包裹
// <serverCommand>。MCP SDK 的 StdioClientTransport 直接吃 {command,args,env,cwd} 去 cross_spawn，
// 这里只产出参数，把进程生命周期（start/close）交给 transport。写墙根 = workspace，会话 temp 一并可写。
export interface SandboxedSpawnParams {
  command: string
  args: string[]
  env: Record<string, string>
  cwd: string
}

export async function buildSandboxedSpawnParams(
  serverCommand: string,
  serverArgs: string[],
  workspace: string,
  baseEnv: NodeJS.ProcessEnv
): Promise<SandboxedSpawnParams> {
  const tempDir = await getSessionTempDir()
  await ensureExtraWritableGrants(workspace, tempDir)
  return {
    command: process.execPath,
    args: [
      await getShimPath(),
      '--workspace', workspace,
      '--temp', tempDir,
      '--mode', 'workspace-write',
      '--', serverCommand, ...serverArgs
    ],
    env: (await sandboxEnv(baseEnv, tempDir)) as Record<string, string>,
    cwd: workspace
  }
}

// ===== 底层 spawn 辅助 =====
function runToBuffer(
  file: string,
  args: string[],
  opts: { cwd: string; env: NodeJS.ProcessEnv; timeout: number }
): Promise<{ stdout: Buffer; stderr: Buffer; code: number; timedOut: boolean }> {
  return new Promise((resolve, reject) => {
    const child = spawn(file, args, {
      cwd: opts.cwd,
      env: opts.env,
      stdio: ['ignore', 'pipe', 'pipe'],
      windowsHide: true
    })
    const chunks: Buffer[][] = [[], []]
    child.stdout.on('data', (d: Buffer) => chunks[0].push(d))
    child.stderr.on('data', (d: Buffer) => chunks[1].push(d))
    // close 回调只带 code/signal，区分不了超时；被 SIGKILL 后 code 为 null → code ?? 1
    let timedOut = false
    const timer = setTimeout(() => {
      timedOut = true
      child.kill('SIGKILL')
    }, opts.timeout)
    child.on('error', (err) => {
      clearTimeout(timer)
      reject(err)
    })
    child.on('close', (code) => {
      clearTimeout(timer)
      resolve({
        stdout: Buffer.concat(chunks[0]),
        stderr: Buffer.concat(chunks[1]),
        code: code ?? 1,
        timedOut
      })
    })
  })
}
