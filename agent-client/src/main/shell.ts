// 解析命令执行用的 shell：Windows 统一用 Windows PowerShell 5.1（沙箱与裸机路径共用同一解析，
// 命令语法契约唯一）。PS5.1 默认按系统代码页（中文系统 GBK）输出，命令执行处注入
// PS_OUTPUT_UTF8 prologue 把控制台输出切到 UTF-8，避免中文乱码。macOS 用 $SHELL（默认 zsh），其他用 /bin/bash。
import { existsSync } from 'fs'

/** PS5.1 命令 prologue：把控制台输出编码切到 UTF-8，供各 -Command 拼接点前置注入。
 *  包 try/catch：沙箱的受限令牌让 PS 进 ConstrainedLanguage，对 [Console] 这类非核心类型
 *  设属性被禁（PropertySetterNotSupportedInConstrainedLanguage），捕获后静默跳过，
 *  否则每条沙箱命令的 stderr 都会多出报错。沙箱中文输出实际由 shim 的
 *  SetConsoleOutputCP(65001)（sandbox.ts）保证。 */
export const PS_OUTPUT_UTF8 = 'try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch {}; '

/** Windows PowerShell 5.1 路径；非 win32 或标准路径不存在时回退 PATH 上的 powershell.exe */
export function resolvePowerShell(): string {
  if (process.platform !== 'win32') return 'powershell.exe'
  const p = 'C:\\Windows\\System32\\WindowsPowerShell\\v1.0\\powershell.exe'
  if (existsSync(p)) return p
  return 'powershell.exe'
}

export function resolveShell(): string {
  if (process.platform === 'win32') {
    return resolvePowerShell()
  }
  if (process.platform === 'darwin') {
    // macOS 用原生 $SHELL（默认 zsh）；系统 bash 是老旧 3.2
    return process.env.SHELL || '/bin/zsh'
  }
  return '/bin/bash'
}
