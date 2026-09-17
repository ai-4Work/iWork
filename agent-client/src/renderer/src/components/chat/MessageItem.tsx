import { useState, memo } from 'react'
import type { Message } from '../../types'
import { useChatStore } from '../../stores/chatStore'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { TypewriterText } from './TypewriterText'
import { SegmentsView, EffectsBlock } from './SegmentsView'

interface Props {
  message: Message
  msgIndex: number
  /** 是否为当前任务最后一条消息（M4 重新生成/继续仅对末位 assistant 显示） */
  isLast?: boolean
  /** user 气泡：若其后紧跟一条 ack 失败的末位 assistant（无 serverId、error 终态），传该 assistant id 以在问题左侧显示“重发” */
  resendAssistantId?: string
  /** 当前任务是否在处理中：并发对话下动作条只应受"当前任务"处理态门控 */
  isCurrentTaskProcessing?: boolean
}

export const MessageItem = memo(function MessageItem({ message, msgIndex, isLast, resendAssistantId, isCurrentTaskProcessing }: Props) {
  const isUser = message.role === 'user'
  const confirmTool = useChatStore((s) => s.confirmTool)
  const skipTool = useChatStore((s) => s.skipTool)
  const stopTools = useChatStore((s) => s.stopTools)
  const selectPlanOption = useChatStore((s) => s.selectPlanOption)
  const answerPlanQuestion = useChatStore((s) => s.answerPlanQuestion)
  const confirmPlan = useChatStore((s) => s.confirmPlan)
  const editPlan = useChatStore((s) => s.editPlan)
  const rejectPlan = useChatStore((s) => s.rejectPlan)
  const regenerateMessage = useChatStore((s) => s.regenerateMessage)
  const continueMessage = useChatStore((s) => s.continueMessage)
  const resendMessage = useChatStore((s) => s.resendMessage)

  const [selectedPlanValue, setSelectedPlanValue] = useState<string | null>(null)
  const [planTextAnswer, setPlanTextAnswer] = useState('')
  const [processCollapsed, setProcessCollapsed] = useState(message.processCollapsed ?? false)

  // User message
  if (isUser) {
    return (
      <div className="flex gap-3 max-w-[740px] self-end flex-row-reverse animate-[msgIn_0.2s_ease-out]">
        <div className="w-[30px] h-[30px] rounded-md bg-[#f0fdf4] text-[#047857] flex items-center justify-center text-[13px] font-semibold flex-shrink-0">Z</div>
        <div className="bg-[#ecfdf5] text-[#064e3b] rounded-[14px_14px_4px_14px] py-3 px-4 text-sm leading-relaxed">
          <ReactMarkdown remarkPlugins={[remarkGfm]}>{message.content}</ReactMarkdown>
          {message.files && message.files.length > 0 && (
            <div className="flex flex-wrap gap-1.5 mt-2 pt-2 border-t border-[#d1fae5]">
              {message.files.map((f, i) => (
                <span key={i} className="inline-flex items-center gap-1 px-2 py-0.5 rounded-md bg-white/60 text-[11px] text-[#047857] font-medium">
                  <svg width="12" height="12" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                    <path d="M3 2h6l4 4v8a1 1 0 01-1 1H3a1 1 0 01-1-1V3a1 1 0 011-1z" />
                    <path d="M9 2v4h4" />
                  </svg>
                  {f.split(/[/\\]/).pop()}
                </span>
              ))}
            </div>
          )}
          {message.skillInvocations && message.skillInvocations.length > 0 && (
            <div className="flex flex-wrap gap-1.5 mt-2 pt-2 border-t border-[#d1fae5]">
              {message.skillInvocations.map((s, i) => (
                <span key={i} className="inline-flex items-center gap-1 px-2 py-0.5 rounded-md bg-white/60 text-[11px] text-[#047857] font-medium">
                  <svg width="12" height="12" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                    <path d="M13 2l-8 8-3-3" />
                  </svg>
                  {s.skill_name}
                </span>
              ))}
            </div>
          )}
        </div>

        {/* ack 失败 → 问题气泡左端显示“重发”：复用原 client_message_id，服务端幂等去重不双跑 */}
        {resendAssistantId && (
          <button
            onClick={() => resendMessage(resendAssistantId)}
            className="w-[24px] h-[24px] rounded-full bg-white border border-[#d1fae5] text-[#64748b] hover:text-[#047857] hover:border-[#047857] flex items-center justify-center transition-colors cursor-pointer shrink-0 self-center"
            title="重发（复用同一幂等键，服务端去重不双跑）"
            aria-label="重发"
          >
            <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round">
              <path d="M21 12a9 9 0 1 1-2.64-6.36" />
              <polyline points="21 3 21 9 15 9" />
            </svg>
          </button>
        )}
      </div>
    )
  }

  // Assistant message
  const hasAnySegment = (message.segments && message.segments.length > 0)
  const hasPending = message.tools?.some(t => t.status === 'pending')
  const effectsRuns = message.effectsRuns || []
  const isCollapsed = processCollapsed && !hasPending

  // M4：末位 assistant 且已收尾（有服务端 message_id、非流式、全局空闲）→ 显示动作条。
  // 重新生成：completed/error/cancelled 都可；继续：仅 error/cancelled（已完成回合不再续）。
  const canReprocess = !!isLast && !!message.serverId && !message.isStreaming && !isCurrentTaskProcessing
  const abnormalEnd = message.serverStatus === 'error' || message.serverStatus === 'cancelled' || message.serverStatus === 'interrupted'

  return (
    <div className="flex gap-3 max-w-[740px] animate-[msgIn_0.2s_ease-out] items-start">
      <div className="w-[30px] h-[30px] rounded-md bg-[#f0fdf4] text-[#a7f3d0] flex items-center justify-center text-[15px] font-semibold flex-shrink-0">AI</div>

      <div className="min-w-0 flex-1">
        <div className="bg-white border border-[#e2e8f0] rounded-[14px_14px_14px_4px] py-3 px-4 text-sm leading-relaxed text-[#0f172a]">

          {/* Segments: text, thinking, tool_calls, plan events — all interleaved in arrival order */}
          {hasAnySegment ? (
            <SegmentsView
              segments={message.segments || []}
              tools={message.tools || []}
              msgIndex={msgIndex}
              isStreaming={message.isStreaming ?? false}
              selectedPlanValue={selectedPlanValue}
              onSelectValue={setSelectedPlanValue}
              textAnswer={planTextAnswer}
              onTextAnswer={setPlanTextAnswer}
              onConfirmPlan={confirmPlan}
              onEditPlan={editPlan}
              onRejectPlan={rejectPlan}
              onSelectOption={selectPlanOption}
              onSubmitAnswer={answerPlanQuestion}
              planStatus={message.planStatus}
              planEditing={message.planEditing}
              processCollapsed={isCollapsed}
              onToggleCollapse={() => setProcessCollapsed(!processCollapsed)}
              onConfirmTool={confirmTool}
              onSkipTool={skipTool}
              onStopTools={stopTools}
            />
          ) : (
            message.content && message.isStreaming ? (
              <TypewriterText text={message.content} isStreaming={true} />
            ) : message.content ? (
              <div className="mt-1 prose-sm max-w-none text-[#0f172a]">
                <ReactMarkdown remarkPlugins={[remarkGfm]}>{message.content}</ReactMarkdown>
              </div>
            ) : message.serverStatus === 'interrupted' ? (
              <div className="text-[#94a3b8] text-[12px] italic">
                （上次运行被中断。可点下方"继续"从已生成处续跑，或"重新生成"重跑该回合）
              </div>
            ) : null
          )}

          {/* Streaming indicator */}
          {message.isStreaming && !message.content && !hasAnySegment && (
            <span className="inline-block w-2 h-4 bg-[#a7f3d0] animate-pulse" />
          )}
        </div>

        {/* D 副作用账本：每次运行独立成块（重新生成不清旧账，跨运行保留）。
            流式/重新生成期间暂不展示，等本轮跑完（isStreaming=false）再整块显示。 */}
        {!message.isStreaming && effectsRuns.length > 0
          ? effectsRuns.map((effs, i) => (
              <div key={`fx-run-${i}`} className="pt-2">
                {effectsRuns.length > 1 && (
                  <div className="mb-1 px-1 text-[10px] font-semibold text-[#94a3b8] tracking-wider">
                    第 {i + 1} 次运行
                  </div>
                )}
                <EffectsBlock effects={effs} />
              </div>
            ))
          : // 旧版本消息：effects 仍以 segment 形式存在 → 兜底渲染
            message.segments?.map((seg, i) => {
              if (seg.type !== 'effects') return null
              return (
                <div key={`fx-legacy-${i}`} className="pt-2">
                  <EffectsBlock effects={seg.effects} />
                </div>
              )
            })}

        {/* M4 动作条：重新生成 / 继续 —— 与气泡左侧对齐，只替换该回合，不新增消息行 */}
        {canReprocess && (
          <div className="flex gap-1.5 pt-1.5 px-4">
            <button
              onClick={() => regenerateMessage(message.id)}
              className="text-[11px] text-[#64748b] hover:text-[#047857] transition-colors cursor-pointer bg-transparent border-none"
              title="截断该回合后重新生成"
            >
              重新生成
            </button>
            {abnormalEnd && (
              <button
                onClick={() => continueMessage(message.id)}
                className="text-[11px] text-[#64748b] hover:text-[#047857] transition-colors cursor-pointer bg-transparent border-none"
                title="从已生成处继续该回合"
              >
                继续
              </button>
            )}
          </div>
        )}
      </div>
    </div>
  )
})
