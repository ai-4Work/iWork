import { submitToolResult, submitReconcileReply, type ToolResult } from './api'
import { ipcClient } from './ipcClient'

// ═══════════════════════════════════════════════════════════════
// C-2 客户端侧：本地执行账 + 断线 outbox + 对账应答
//   - executedLedger（内存）：requestId(==invocation_id) → 本次成功执行的 ToolResult。
//     服务端对账（client.tool_reconcile）到达时，据此答 executed 并补投结果；
//     若执行的是写类且提交 POST 已丢，账仍持有结果 → 应答 executed，服务端收口，杜绝"误判失败后重执行"。
//   - outbox（持久化到 electron-store ns=tools）：提交 POST 失败的 ToolResult 排队重放，
//     重连/启动时 flush；服务端 duplicate 兜底 → 同一 invocation 至多被消费一次。
// ═══════════════════════════════════════════════════════════════

export interface OutboxEntry {
  id: string
  sessionId: string
  requestId: string
  result: ToolResult
  queuedAt: number
}

const OUTBOX_NS = 'tools'
const OUTBOX_KEY = 'outbox'
const LEDGER_CAP = 500

// ---- 本地执行账（内存） ----
const executedLedger = new Map<string, ToolResult>()

export function recordExecutedTool(requestId: string, result: ToolResult): void {
  executedLedger.set(requestId, result)
  if (executedLedger.size > LEDGER_CAP) {
    const oldest = executedLedger.keys().next().value
    if (oldest !== undefined) executedLedger.delete(oldest)
  }
}

export function lookupExecutedTool(requestId: string): ToolResult | undefined {
  return executedLedger.get(requestId)
}

export function dropExecutedTool(requestId: string): void {
  executedLedger.delete(requestId)
}

// ---- outbox（持久化） ----

async function loadOutbox(): Promise<OutboxEntry[]> {
  try {
    const v = await ipcClient.storage.get(OUTBOX_NS, OUTBOX_KEY)
    return Array.isArray(v) ? (v as OutboxEntry[]) : []
  } catch {
    return []
  }
}

async function saveOutbox(list: OutboxEntry[]): Promise<void> {
  try {
    await ipcClient.storage.set(OUTBOX_NS, OUTBOX_KEY, list)
  } catch (err) {
    console.error('Tool outbox persist failed:', err)
  }
}

/** 提交失败时把结果排入持久化 outbox（同 invocation 幂等），随后触发重放。 */
export async function enqueueToolResult(
  sessionId: string,
  requestId: string,
  result: ToolResult
): Promise<void> {
  const list = await loadOutbox()
  if (list.some((e) => e.sessionId === sessionId && e.requestId === requestId)) return
  list.push({
    id: crypto.randomUUID(),
    sessionId,
    requestId,
    result,
    queuedAt: Date.now()
  })
  await saveOutbox(list)
  void flushToolOutbox()
}

let flushing = false

/** 重连 / 启动时调用：把 outbox 里未回投的结果补投；服务端 duplicate 时正常移除。 */
export async function flushToolOutbox(): Promise<void> {
  if (flushing) return
  flushing = true
  try {
    const list = await loadOutbox()
    if (list.length === 0) return
    const remain: OutboxEntry[] = []
    for (const e of list) {
      try {
        await submitToolResult(e.sessionId, e.requestId, e.result) // 含 duplicate(200) → 移除
      } catch {
        remain.push(e) // 仍失败：保留待下次
      }
    }
    if (remain.length !== list.length) await saveOutbox(remain)
  } finally {
    flushing = false
  }
}

/** 提交并回填：优先直投；失败落 outbox。成功后账里若还留着该 request 则清理。 */
export async function submitWithOutbox(
  sessionId: string,
  requestId: string,
  result: ToolResult
): Promise<void> {
  try {
    await submitToolResult(sessionId, requestId, result)
    dropExecutedTool(requestId)
  } catch {
    await enqueueToolResult(sessionId, requestId, result)
  }
}

/** 服务端对账询问：本地已执行则补投真实结果，否则如实声明 unknown（不瞎编、不自动重执行）。 */
export async function answerClientReconcile(
  sessionId: string,
  requestId: string
): Promise<void> {
  const executed = lookupExecutedTool(requestId)
  try {
    if (executed) {
      await submitReconcileReply(sessionId, requestId, 'executed', executed)
      dropExecutedTool(requestId)
    } else {
      await submitReconcileReply(sessionId, requestId, 'unknown')
    }
  } catch (err) {
    console.error('Reconcile reply failed:', err)
  }
}
