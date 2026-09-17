import { useState, useCallback, useRef, useEffect, memo } from 'react'
import type { MessageSegment, ToolCall, PlanEvent, SideEffectItem } from '../../types'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { TypewriterText } from './TypewriterText'

// Shared renderer for interleaved message segments (text, thinking, tool calls, plan
// events, system status). Used by both the single-chat MessageItem and the multi-agent
// panel so both surfaces render plan/build/tool interactions identically.

/** 参数值原样摊在卡片上会把 write_file 的 content 这类正文整段铺出来，过长只给体量提示 */
const INLINE_VALUE_MAX = 60
function inlineValue(v: unknown): string {
  const s = typeof v === 'string' ? v : (JSON.stringify(v) ?? '')
  if (s.length > INLINE_VALUE_MAX || s.includes('\n')) return `«${s.length} 字符»`
  return /\s/.test(s) ? `"${s}"` : s
}

/** 卡片调用块那一行：bash 类显示 `$ <command>`；无 command 的工具（文件/MCP/skill）
 *  合成 `$ <tool> k=v …`。两类走同一个深色块，避免只有 bash 有深色框。 */
function invocationLine(tool: ToolCall): string | null {
  if (tool.command) return `$ ${tool.command}`
  const input = tool.input
  if (!input || typeof input !== 'object') return null
  const parts = Object.entries(input)
    .filter(([, v]) => v !== undefined && v !== null && v !== '')
    .map(([k, v]) => `${k}=${inlineValue(v)}`)
  if (parts.length === 0) return null
  return `$ ${tool.name} ${parts.join(' ')}`
}

interface SegmentsViewProps {
  segments: MessageSegment[]
  tools: ToolCall[]
  msgIndex: number
  isStreaming: boolean
  /** When true, the last text segment is still growing and should render as plain text (no markdown parse). */
  streamingText?: boolean
  selectedPlanValue: string | null
  onSelectValue: (v: string) => void
  textAnswer: string
  onTextAnswer: (v: string) => void
  onConfirmPlan: () => void
  onEditPlan: (msgIdx: number) => void
  onRejectPlan: () => void
  onSelectOption: (msgIdx: number, v: string) => void
  onSubmitAnswer: (msgIdx: number, textAnswer?: string) => void
  planStatus?: string
  planEditing?: boolean
  processCollapsed: boolean
  onToggleCollapse: () => void
  /** 按卡片确认/跳过（一轮可同时下发多张卡）；onStopTools 仍是整批终止语义 */
  onConfirmTool: (toolId: string) => void
  onSkipTool: (toolId: string) => void
  onStopTools: () => void
}

export function SegmentsView({
  segments,
  tools,
  msgIndex,
  isStreaming,
  streamingText = false,
  selectedPlanValue,
  onSelectValue,
  textAnswer,
  onTextAnswer,
  onConfirmPlan,
  onEditPlan,
  onRejectPlan,
  onSelectOption,
  onSubmitAnswer,
  planStatus,
  planEditing,
  processCollapsed,
  onToggleCollapse,
  onConfirmTool,
  onSkipTool,
  onStopTools
}: SegmentsViewProps) {
  // Build a map for quick tool lookup by id
  const toolMap = new Map<string, ToolCall>()
  for (const t of tools) {
    toolMap.set(t.id, t)
  }

  // 工具运行环境的角标数据：沙箱运行 → 沙箱运行完成/失败 / 裸机运行 → 裸机运行完成/失败。
  // 仅在 tool.sandboxed 已知（client.tool_request 下发 policy 或 file:exec 回填）且 running/终态时返回。
  const getRunChip = (tool: ToolCall): { label: string; cls: string } | null => {
    if (tool.sandboxed === undefined) return null
    const failed = tool.status === 'failed'
    const done = tool.status === 'done'
    if (tool.status !== 'running' && !done && !failed) return null
    if (tool.sandboxed) {
      if (failed) return { label: '沙箱运行失败', cls: 'text-[#dc2626] bg-[#fef2f2] border-[#fecaca]' }
      return done
        ? { label: '沙箱运行完成', cls: 'text-black bg-[#10b981]/15 border-[#10b981]/40' }
        : { label: '沙箱运行中...', cls: 'text-black bg-[#0ea5e9]/15 border-[#0ea5e9]/40' }
    }
    if (failed) return { label: '裸机运行失败', cls: 'text-[#dc2626] bg-[#fef2f2] border-[#fecaca]' }
    return done
      ? { label: '裸机运行完成', cls: 'text-black bg-[#e2e8f0] border-[#cbd5e1]' }
      : { label: '裸机运行中...', cls: 'text-black bg-[#e2e8f0] border-[#cbd5e1]' }
  }

  // 工具名同一行右侧的运行环境角标（沙箱/裸机），所有客户端工具共用；无 command 的文件类工具也显示
  const renderRunChip = (tool: ToolCall) => {
    const chip = getRunChip(tool)
    if (!chip) return null
    return (
      <span className={`px-1.5 py-px rounded text-[9px] leading-4 font-sans font-medium border whitespace-nowrap ${chip.cls}`}>
        {chip.label}
      </span>
    )
  }

  // `$ …` 深色代码块（角标已移到工具名行，块内不再带角标）
  const renderInvocationBlock = (tool: ToolCall) => {
    const line = invocationLine(tool)
    if (!line) return null
    return (
      <div className="mt-1 py-1.5 px-2.5 bg-[#1e293b] text-[#a7f3d0] rounded text-[11px] font-mono whitespace-pre-wrap">
        {line}
      </div>
    )
  }

  // Find the index of the last text segment (for typewriter animation)
  const lastTextSegIdx = (() => {
    for (let i = segments.length - 1; i >= 0; i--) {
      if (segments[i].type === 'text') return i
    }
    return -1
  })()

  // Track which text segments have finished their typewriter animation
  const [doneTextIndices, setDoneTextIndices] = useState<Set<number>>(new Set())

  const handleTextDone = useCallback((segIdx: number) => {
    setDoneTextIndices((prev) => {
      if (prev.has(segIdx)) return prev
      const next = new Set(prev)
      next.add(segIdx)
      return next
    })
  }, [])

  const isTextComplete = (segIdx: number) => {
    if (!isStreaming) return true
    if (segIdx !== lastTextSegIdx) return true
    return doneTextIndices.has(segIdx)
  }

  // Non-text segments should only show when all preceding text is complete
  const canShowNonText = (nonTextIdx: number) => {
    for (let j = 0; j < nonTextIdx; j++) {
      if (segments[j].type === 'text' && !isTextComplete(j)) {
        return false
      }
    }
    return true
  }

  // Render a single tool by looking up its live state from the tools map
  const renderTool = (toolCallId: string | undefined, key: string | number) => {
    const tool = toolCallId ? toolMap.get(toolCallId) : undefined
    if (!tool) return null

    // Build mode tools have reasoning (the question from AI)
    const hasReasoning = tool.detail && !tool.detail.startsWith('{')

    // 工具运行环境角标（沙箱/裸机）是否出现在工具名一行；出现时不再重复显示右侧通用 执行中…/完成 pill
    const runChipVisible = getRunChip(tool) !== null

    return (
      <div key={key} className="mb-1.5 not-italic text-[#0f172a]">
        {/* Tool reasoning as a question card */}
        {hasReasoning && (
          <div className="p-3 bg-[#f0f9ff] border border-[#bae6fd] rounded-[8px]">
            <div className="flex items-center justify-between gap-2 mb-1">
              <div className="text-[10px] font-semibold text-[#0369a1] uppercase tracking-wider">执行步骤: {tool.name}</div>
              {renderRunChip(tool)}
            </div>
            <div className="text-xs text-[#0f172a] mb-1.5 leading-relaxed">{tool.detail}</div>
            {renderInvocationBlock(tool)}
            {/* User answer */}
            {tool.status !== 'pending' && (
              <div className="mt-2 pt-2 border-t border-[#bae6fd]">
                <span className={`text-[11px] font-medium px-2.5 py-0.5 rounded-md inline-flex items-center gap-1 ${
                  tool.status === 'skipped'
                    ? 'text-[#b45309] bg-[#fffbeb] border border-[#fcd34d]'
                    : tool.status === 'failed'
                      ? 'text-[#dc2626] bg-[#fef2f2] border border-[#fecaca]'
                      : 'text-[#047857] bg-[#d1fae5] border border-[#a7f3d0]'
                }`}>
                  {tool.status === 'failed' ? (
                    <>
                      <svg width="11" height="11" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="2.5"><path d="M8 4v5M8 11.5v.5" /></svg>
                      执行失败
                    </>
                  ) : tool.status === 'skipped' ? (
                    <>
                      <svg width="11" height="11" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="2.5"><path d="M4 4l8 8M12 4l-8 8" /></svg>
                      已跳过
                    </>
                  ) : (
                    <>
                      <svg width="11" height="11" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="2.5"><path d="M3 8l4 4 6-8" /></svg>
                      已确认，正在执行...
                    </>
                  )}
                </span>
              </div>
            )}
          </div>
        )}

        {/* No reasoning — simple tool card (agent.tool_call, etc.) */}
        {!hasReasoning && (
          <div className="flex items-start gap-2.5 py-2 border-b border-[#f1f5f9] last:border-b-0">
            <svg className="w-4 h-4 flex-shrink-0 mt-0.5 text-[#94a3b8]" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="2"><circle cx="8" cy="8" r="6" /><path d="M8 5v3l2 2" /></svg>
            <div className="flex-1 min-w-0">
              <div className="font-semibold text-xs text-[#0f172a]">{tool.name}</div>
              {renderInvocationBlock(tool)}
            </div>
            {runChipVisible ? renderRunChip(tool) : (
              tool.status !== 'pending' && (
                <span className={`text-[11px] px-2 py-0.5 rounded-lg font-medium flex-shrink-0 ${tool.status === 'running' ? 'text-[#b45309] bg-[#fffbeb]'
                  : tool.status === 'failed' ? 'text-[#dc2626] bg-[#fef2f2]'
                    : 'text-[#047857] bg-[#ecfdf5]'
                  }`}>
                  {tool.status === 'running' ? '执行中...' : tool.status === 'failed' ? '失败' : '完成'}
                </span>
              )
            )}
          </div>
        )}

        {/* Confirm/Skip buttons — 仅当需要用户审批时显示（auto-execute 的 running 不显示） */}
        {tool.status === 'pending' && tool.approvalRequired !== false && (
          <div className="flex gap-1.5 mt-1 flex-wrap">
            <button onClick={() => onConfirmTool(tool.id)} className="px-3 py-1 rounded text-[11px] font-medium text-[#047857] border border-[#a7f3d0] bg-[#f0fdf4] hover:bg-[#a7f3d0] transition-colors cursor-pointer">确认</button>
            <button onClick={() => onSkipTool(tool.id)} className="px-3 py-1 rounded text-[11px] font-medium text-[#b45309] border border-[#fcd34d] bg-[#fffbeb] hover:bg-[#fde68a] transition-colors cursor-pointer">跳过</button>
            <button onClick={() => onStopTools()} className="px-3 py-1 rounded text-[11px] font-medium text-[#b91c1c] border border-[#fecaca] bg-[#fef2f2] hover:bg-[#fecaca] transition-colors cursor-pointer">终止</button>
          </div>
        )}

        {/* Tool result */}
        {tool.result && (
          <div className="mt-1 py-1.5 px-2.5 bg-[#f8fafc] rounded text-[11px] font-mono whitespace-pre-wrap max-h-[100px] overflow-y-auto border border-[#f1f5f9] ml-6">
            {tool.result}
          </div>
        )}
      </div>
    )
  }

  // Build a flat render list: contiguous thinking segments are grouped into one box;
  // tool calls and other segments render inline in arrival order.
  type RenderItem =
    | { kind: 'thinking'; segs: { seg: typeof segments[0]; idx: number }[] }
    | { kind: 'other'; seg: typeof segments[0]; idx: number }

  const renderItems: RenderItem[] = []
  let pending: { seg: typeof segments[0]; idx: number }[] = []
  for (let i = 0; i < segments.length; i++) {
    const seg = segments[i]
    if (seg.type === 'thinking') {
      pending.push({ seg, idx: i })
    } else {
      if (pending.length > 0) {
        renderItems.push({ kind: 'thinking', segs: pending })
        pending = []
      }
      renderItems.push({ kind: 'other', seg, idx: i })
    }
  }
  if (pending.length > 0) {
    renderItems.push({ kind: 'thinking', segs: pending })
  }

  return (
    <>
      {renderItems.map((item, ri) => {
        if (item.kind === 'thinking') {
          const firstIdx = item.segs[0].idx
          if (!canShowNonText(firstIdx)) return null
          return (
            <ThinkingBox
              key={`thinking-${ri}`}
              segs={item.segs}
              processCollapsed={processCollapsed}
              onToggleCollapse={onToggleCollapse}
            />
          )
        }

        // Other segments: text, tool_call, plan, system_status — all inline
        const seg = item.seg
        const i = item.idx

        // Text segment
        if (seg.type === 'text') {
          const isLastText = i === lastTextSegIdx
          if (isLastText && isStreaming) {
            return <TypewriterText key={`txt-${i}`} text={seg.content} isStreaming={true} onDone={() => handleTextDone(i)} />
          }
          if (isLastText && streamingText) {
            return (
              <div key={`txt-${i}`} className="mt-1 mb-2 text-xs leading-[1.55] whitespace-pre-wrap break-words text-[#0f172a]">
                {seg.content}
              </div>
            )
          }
          return <MarkdownBlock key={`txt-${i}`} content={seg.content} />
        }

        // Tool call — render inline without wrapper
        if (seg.type === 'tool_call') {
          if (!canShowNonText(i)) return null
          return renderTool(seg.toolCallId, `tool-${i}`)
        }

        // Plan events — only show when preceding text is complete
        if (!canShowNonText(i)) return null

        const event = seg as PlanEvent

        if (event.type === 'generated') {
          return (
            <div key={event.id} className="mt-0 -mx-1 mb-1 p-4 rounded-[10px] border border-l-[3px] bg-[#f0fdf4] border-[#a7f3d0] border-l-[#10b981]">
              <div className="text-[10px] font-semibold text-[#047857] uppercase tracking-wider mb-2">执行计划</div>
              {planStatus === 'pending' && !planEditing && (
                <div className="flex gap-2 flex-wrap">
                  <button onClick={() => onConfirmPlan()} className="px-[18px] py-[7px] rounded-md text-xs font-medium bg-[#0f172a] text-white border border-[#0f172a] hover:bg-[#334155] transition-colors cursor-pointer">确认计划</button>
                  <button onClick={() => onEditPlan(msgIndex)} className="px-[18px] py-[7px] rounded-md text-xs font-medium bg-white text-[#64748b] border border-[#e2e8f0] hover:bg-[#f1f5f9] hover:text-[#0f172a] transition-colors cursor-pointer">编辑</button>
                  <button onClick={() => onRejectPlan()} className="px-[18px] py-[7px] rounded-md text-xs font-medium bg-white text-[#b91c1c] border border-[#fecaca] hover:bg-[#fef2f2] transition-colors cursor-pointer">拒绝</button>
                </div>
              )}
              {planEditing && (
                <span className="text-xs text-[#0369a1] font-medium flex items-center gap-1.5">
                  <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="2"><path d="M2 14h2l8-8-2-2-8 8v2z" /><path d="M12 3l2 2" /></svg>
                  正在右侧面板编辑计划...
                </span>
              )}
            </div>
          )
        }
        if (event.type === 'confirmed') {
          return (
            <div key={event.id} className="-mx-1 mb-1 p-3 rounded-[10px] border border-l-[3px] bg-[#ecfdf5] border-[#a7f3d0] border-l-[#10b981]">
              <span className="text-xs text-[#047857] font-medium bg-[#d1fae5] px-2.5 py-1 rounded-md inline-flex items-center gap-1">
                <svg width="12" height="12" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="2.5"><path d="M3 8l4 4 6-8" /></svg>
                计划已确认，正在自动执行...
              </span>
            </div>
          )
        }
        if (event.type === 'rejected') {
          return (
            <div key={event.id} className="-mx-1 mb-1 p-3 rounded-[10px] border border-l-[3px] bg-[#fef2f2] border-[#fecaca] border-l-[#ef4444]">
              <span className="text-xs text-[#b91c1c] font-medium bg-[#fee2e2] px-2.5 py-1 rounded-md inline-flex items-center gap-1">
                <svg width="12" height="12" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="2.5"><path d="M4 4l8 8M12 4l-8 8" /></svg>
                计划已取消
              </span>
            </div>
          )
        }
        if (event.type === 'edited') {
          return (
            <div key={event.id} className="-mx-1 mb-1 p-3 rounded-[10px] border border-l-[3px] bg-[#f0fdf4] border-[#a7f3d0] border-l-[#10b981]">
              <span className="text-xs text-[#0369a1] font-medium bg-[#dbeafe] px-2.5 py-1 rounded-md inline-flex items-center gap-1">
                <svg width="12" height="12" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="2"><path d="M2 14h2l8-8-2-2-8 8v2z" /><path d="M12 3l2 2" /></svg>
                计划已编辑
              </span>
            </div>
          )
        }
        if (event.type === 'question') {
          const isConfirm = event.input_type === 'confirm'
          const options = isConfirm ? ['是 / 确定', '否 / 取消'] : (event.options || [])
          if (event.answer) {
            return (
              <div key={event.id} className="mb-1.5 p-3 bg-[#f0f9ff] border border-[#bae6fd] rounded-[8px]">
                <div className="text-[10px] font-semibold text-[#0369a1] uppercase tracking-wider mb-1">需求澄清</div>
                <div className="text-xs text-[#0f172a] mb-1.5 leading-relaxed">{event.question}</div>
                <div className="text-xs text-[#0369a1] font-medium bg-white border border-[#bae6fd] rounded-md px-2.5 py-1.5 inline-block">
                  回答: {event.answer}
                </div>
              </div>
            )
          }
          return (
            <div key={event.id} className="mb-1.5 p-3 bg-[#f0f9ff] border border-[#bae6fd] rounded-[8px]">
              <div className="text-[10px] font-semibold text-[#0369a1] uppercase tracking-wider mb-1">需求澄清</div>
              <div className="text-xs text-[#0f172a] mb-2.5 leading-relaxed">{event.question}</div>
              {event.input_type === 'text' ? (
                <>
                  <textarea
                    className="w-full px-3 py-2 border border-[#bae6fd] rounded-md text-[13px] text-[#0f172a] outline-none resize-y mb-2 focus:border-[#0ea5e9] focus:shadow-[0_0_0_3px_rgba(14,165,233,0.15)] transition-colors"
                    placeholder="输入你的回答..."
                    rows={2}
                    value={textAnswer}
                    onChange={(e) => onTextAnswer(e.target.value)}
                    onKeyDown={(e) => {
                      if (e.key === 'Enter' && !e.shiftKey && textAnswer.trim()) {
                        e.preventDefault()
                        onSelectValue(textAnswer.trim())
                        onSubmitAnswer(msgIndex, textAnswer.trim())
                        onTextAnswer('')
                      }
                    }}
                  />
                  <button
                    onClick={() => {
                      onSubmitAnswer(msgIndex, textAnswer.trim())
                      onTextAnswer('')
                    }}
                    disabled={!textAnswer.trim()}
                    className="px-[18px] py-[7px] rounded-md text-xs font-medium bg-[#0f172a] text-white hover:bg-[#334155] transition-colors border-none cursor-pointer disabled:opacity-40"
                  >
                    提交
                  </button>
                </>
              ) : (
                <>
                  <div className="flex flex-col gap-1.5 mb-2.5">
                    {options.map(opt => {
                      const isSelected = selectedPlanValue === opt
                      return (
                        <button
                          key={opt}
                          type="button"
                          onClick={() => {
                            onSelectValue(opt)
                            onSelectOption(msgIndex, opt)
                          }}
                          className={`px-3 py-2 border rounded-md text-[13px] text-left cursor-pointer flex items-center gap-2 ${isSelected
                              ? 'border-[#0ea5e9] bg-[#0ea5e9]/10 text-[#0369a1] font-semibold shadow-[0_0_0_1px_#0ea5e9]'
                              : 'border-[#bae6fd] bg-white text-[#0f172a] hover:border-[#0ea5e9] hover:bg-[#f0f9ff]'
                            }`}
                        >
                          <span className={`flex-shrink-0 w-4 h-4 rounded-full border-2 flex items-center justify-center transition-none ${isSelected ? 'border-[#0ea5e9] bg-[#0ea5e9]' : 'border-[#94a3b8]'
                            }`}>
                            {isSelected && (
                              <svg width="10" height="10" viewBox="0 0 16 16" fill="none" stroke="white" strokeWidth="3"><path d="M3 8l4 4 6-8" /></svg>
                            )}
                          </span>
                          {opt}
                        </button>
                      )
                    })}
                  </div>
                  <button
                    type="button"
                    onClick={() => {
                      onSubmitAnswer(msgIndex, selectedPlanValue || undefined)
                      onSelectValue('')
                    }}
                    disabled={!selectedPlanValue}
                    className="px-[18px] py-[7px] rounded-md text-xs font-medium bg-[#0f172a] text-white hover:bg-[#334155] transition-colors border-none cursor-pointer disabled:opacity-40"
                  >
                    提交
                  </button>
                </>
              )}
            </div>
          )
        }

        if (seg.type === 'system_status') {
          return (
            <div key={`status-${i}`} className="mb-1.5 p-3 bg-[#fefce8] border border-[#fde68a] rounded-[8px]">
              <div className="text-[10px] font-semibold text-[#a16207] uppercase tracking-wider mb-1">系统状态</div>
              <div className="text-xs text-[#0f172a] leading-relaxed">{seg.message}</div>
            </div>
          )
        }

        if (seg.type === 'effects') {
          // effects 段不再内联渲染，改由 MessageItem 在气泡下方统一展示
          return null
        }

        return null
      })}
    </>
  )
}

// Memoized markdown renderer: completed text segments don't re-parse their markdown
// when a later streaming segment updates (the content string is referentially stable).
const MarkdownBlock = memo(function MarkdownBlock({ content }: { content: string }) {
  return (
    <div className="mt-1 mb-2 prose-sm max-w-none text-[#0f172a]">
      <ReactMarkdown remarkPlugins={[remarkGfm]}>{content}</ReactMarkdown>
    </div>
  )
})

// A collapsible thinking box that auto-scrolls its inner content to the bottom as
// thinking deltas stream in (the box is capped at max-h-[300px] with its own scroll).
function ThinkingBox({ segs, processCollapsed, onToggleCollapse }: {
  segs: { seg: MessageSegment; idx: number }[]
  processCollapsed: boolean
  onToggleCollapse: () => void
}) {
  const contentRef = useRef<HTMLDivElement>(null)
  const rafRef = useRef<number | null>(null)
  const totalLength = segs.reduce((sum, { seg }) => sum + (seg.type === 'thinking' ? seg.content.length : 0), 0)

  // Auto-scroll the thinking box to the bottom as content grows (rAF-throttled so
  // rapid thinking deltas collapse into a single scroll per frame).
  useEffect(() => {
    const el = contentRef.current
    if (!el || processCollapsed) return
    if (rafRef.current != null) return
    rafRef.current = requestAnimationFrame(() => {
      rafRef.current = null
      el.scrollTop = el.scrollHeight
    })
  }, [totalLength, processCollapsed])

  useEffect(() => {
    return () => {
      if (rafRef.current != null) cancelAnimationFrame(rafRef.current)
    }
  }, [])

  return (
    <div className={`mt-2.5 border border-[#e2e8f0] rounded-md overflow-hidden ${processCollapsed ? 'section-collapsed' : ''}`}>
      <div
        onClick={onToggleCollapse}
        className="flex items-center gap-2 px-3 py-2 cursor-pointer text-xs font-medium text-[#64748b] bg-[#f8fafc] select-none hover:bg-[#f1f5f9] transition-colors"
      >
        <span className={`inline-block transition-transform text-[10px] text-[#94a3b8] ${processCollapsed ? '-rotate-90' : ''}`}>▼</span>
        思考过程
      </div>
      {!processCollapsed && (
        <div ref={contentRef} className="px-3.5 py-2.5 text-[13px] text-[#64748b] leading-relaxed bg-white border-t border-[#f1f5f9] max-h-[300px] overflow-y-auto">
          {segs.map(({ seg, idx }) => (
            <div key={`think-${idx}`} className="mb-2.5 last:mb-0">{seg.type === 'thinking' ? seg.content : null}</div>
          ))}
        </div>
      )}
    </div>
  )
}

// ── D 副作用账本：已执行清单（如实交代，已执行、未回滚）─────────────────

function effectSummary(e: SideEffectItem): string {
  const input = e.input || {}
  if (typeof input.command === 'string' && input.command) return input.command
  if (typeof input.path === 'string' && input.path) return input.path
  const keys = Object.keys(input)
  if (keys.length === 0) return '(无参数)'
  return JSON.stringify(input).slice(0, 120)
}

function effectTime(e: SideEffectItem): string {
  const raw = e.completed_at || e.created_at
  if (!raw) return ''
  const d = new Date(raw)
  if (Number.isNaN(d.getTime())) return ''
  return d.toLocaleTimeString()
}

function idemTag(idempotency?: string): { label: string; cls: string } | null {
  if (idempotency === 'read-only') return null        // 只读行已有「只读」标记，不需要重放提示
  // 幂等档只作信息提示，用中性灰避免与下方的状态色（完成/失败/跳过/未知）混淆
  return {
    label: idempotency === 'idempotent' ? '可安全重放' : '不可自动重放',
    cls: 'bg-white text-[#64748b] border-[#e2e8f0]',
  }
}

type EffectTone = 'done' | 'error' | 'skipped' | 'unknown'

// 状态判定优先用账本 state（四态都随行下发）：skipped → 跳过；issued/superseded → 未知；
// completed 再看 result.success 分成功/失败。state 缺失（旧响应）时退回按终态痕迹推断。
// 配色与整体页面统一：浅绿=完成、红=失败、蓝=跳过、黄=未知。
function effectTone(e: SideEffectItem): EffectTone {
  if (e.state === 'skipped') return 'skipped'
  if (e.state === 'issued' || e.state === 'superseded') return 'unknown'
  if (e.state === 'completed') {
    const r = e.result as { success?: unknown } | null | undefined
    return r && typeof r === 'object' && r.success === false ? 'error' : 'done'
  }
  if (e.error) return 'error'
  const r = e.result as { success?: unknown } | null | undefined
  if (r && typeof r === 'object' && 'success' in r) {
    return r.success === false ? 'error' : 'done'
  }
  if (e.completed_at) return 'done'
  return 'unknown'
}

const EFFECT_TONE_META: Record<EffectTone, { label: string; pill: string; chip: string }> = {
  done: {
    label: '完成',
    pill: 'bg-[#ecfdf5] text-[#047857] border-[#a7f3d0]',
    chip: 'bg-[#ecfdf5] text-[#047857] border border-[#a7f3d0]',
  },
  error: {
    label: '失败',
    pill: 'bg-[#fef2f2] text-[#dc2626] border-[#fecaca]',
    chip: 'bg-[#fef2f2] text-[#dc2626] border border-[#fecaca]',
  },
  skipped: {
    label: '跳过',
    pill: 'bg-[#eff6ff] text-[#1d4ed8] border-[#bfdbfe]',
    chip: 'bg-[#eff6ff] text-[#1d4ed8] border border-[#bfdbfe]',
  },
  unknown: {
    label: '未知',
    pill: 'bg-[#fffbeb] text-[#d97706] border-[#fde68a]',
    chip: 'bg-[#fffbeb] text-[#d97706] border border-[#fde68a]',
  },
}

export function EffectsBlock({ effects }: { effects: SideEffectItem[] }) {
  const n = effects.length
  // 只有 completed ∧ 写类 才确定是「已生效写」，需要如实交代"已执行、未回滚"；
  // 只读动作与 skipped/issued/superseded 都只是过程记录，不背这句交代。
  const hasAppliedWrite = effects.some((e) => e.state === 'completed' && e.side_effect)
  return (
    <div className="mb-1.5 p-3 bg-[#f8fafc] border border-[#e2e8f0] rounded-[8px]">
      <div className="flex items-center gap-2 mb-1.5">
        <span className="text-[10px] font-semibold text-[#64748b] uppercase tracking-wider">
          工具动作 {n} 项
        </span>
        {hasAppliedWrite && (
          <span className="text-[10px] text-[#b45309] bg-[#fffbeb] border border-[#fde68a] px-1.5 py-0.5 rounded">已执行，未回滚</span>
        )}
      </div>
      <ul className="space-y-1">
        {effects.map((e) => {
          const meta = EFFECT_TONE_META[effectTone(e)]
          const tag = idemTag(e.idempotency)
          const t = effectTime(e)
          return (
            <li key={e.invocation_id} className="flex items-center gap-2 text-xs text-[#0f172a] leading-snug">
              <span className={`font-mono px-1.5 py-0.5 rounded border shrink-0 ${meta.pill}`}>
                {e.tool_name}
              </span>
              {e.side_effect === false && (
                <span className="text-[10px] px-1.5 py-0.5 rounded border shrink-0 bg-white text-[#94a3b8] border-[#e2e8f0]">只读</span>
              )}
              <span className="text-[#334155] truncate flex-1">{effectSummary(e)}</span>
              <span className={`text-[10px] px-1.5 py-0.5 rounded shrink-0 font-medium ${meta.chip}`}>
                {meta.label}
              </span>
              {tag && (
                <span className={`text-[10px] px-1.5 py-0.5 rounded border shrink-0 ${tag.cls}`}>{tag.label}</span>
              )}
              {t && <span className="text-[10px] text-[#94a3b8] shrink-0">{t}</span>}
            </li>
          )
        })}
      </ul>
    </div>
  )
}
