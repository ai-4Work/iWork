import { useRef, useEffect, useState, useCallback, memo } from 'react'
import { useMultiAgentStore } from '../../stores/multiAgentStore'
import { useSettingsStore } from '../../stores/settingsStore'
import { useModeStore } from '../../stores/modeStore'
import { useTaskStore } from '../../stores/taskStore'
import { ChatInput } from './ChatInput'
import { SegmentsView } from './SegmentsView'
import { useAutoScroll } from './useAutoScroll'
import { sendChatMessage, createSession, CLIENT_TOOLS } from '../../services/api'
import type { AgentColumn, MultiAgentMessage, AppMode } from '../../types'

const STATUS_TEXT: Record<string, string> = {
  idle: '空闲',
  thinking: '思考中...',
  running: '执行中...',
  done: '已完成',
  waiting: '等待子任务...',
  failed: '已失败'
}

export function MultiAgentPanel() {
  const session = useMultiAgentStore((s) => s.session)
  const toggleColumn = useMultiAgentStore((s) => s.toggleColumn)
  const showAllColumns = useMultiAgentStore((s) => s.showAllColumns)
  const globalSettings = useSettingsStore((s) => s.settings)
  const globalInputMode = useModeStore((s) => s.inputMode)
  const globalSceneMode = useModeStore((s) => s.sceneMode)

  // Toolbar state — isolated from global settings for multi-agent context
  const [leadWorkspace, setLeadWorkspace] = useState(globalSettings.workspacePath)
  const [leadModel, setLeadModel] = useState(globalSettings.model)
  const [leadInputMode, setLeadInputMode] = useState<AppMode>(globalInputMode)

  const scrollRef = useRef<HTMLDivElement>(null)
  const [showLeftHint, setShowLeftHint] = useState(false)
  const [showRightHint, setShowRightHint] = useState(false)

  // All hooks must be called unconditionally before any early return
  const visibleAgents = session ? session.agents.filter(a => !a._hidden) : []
  const isScrollable = visibleAgents.length >= 5

  const checkScrollHints = () => {
    const el = scrollRef.current
    if (!el || !isScrollable) return
    setShowLeftHint(el.scrollLeft > 8)
    setShowRightHint(el.scrollLeft < el.scrollWidth - el.clientWidth - 8)
  }

  useEffect(() => {
    if (!isScrollable) return
    const el = scrollRef.current
    if (!el) return
    el.addEventListener('scroll', checkScrollHints, { passive: true })
    checkScrollHints()
    return () => el.removeEventListener('scroll', checkScrollHints)
  }, [isScrollable])

  if (!session) return null

  const hiddenAgents = session.agents.filter(a => a._hidden)
  const hasHidden = hiddenAgents.length > 0
  const numCols = Math.min(visibleAgents.length, 4)

  const scrollColumns = (dir: 'left' | 'right') => {
    const el = scrollRef.current
    if (!el) return
    el.scrollBy({ left: dir === 'left' ? -300 : 300, behavior: 'smooth' })
  }

  const handleLeadSend = useCallback(async (text: string, files?: string[], skillInvocations?: { skill_id: string; skill_name: string }[]) => {
    const ma = useMultiAgentStore.getState()
    const sess = ma.session
    if (!text.trim() || !sess) return
    const leadId = sess.leadAgentId
    ma.setProcessing(true)

    ma.addMessage(leadId, {
      agentId: leadId,
      role: 'user',
      content: text.trim(),
      type: 'text'
    })

    try {
      // Use the task's agentId (set by handleUseTeam when clicking "使用") for
      // both session creation and message sending — it uses the team's ID which
      // the backend correctly resolves to the lead agent via agent_type="team".
      const taskStore = useTaskStore.getState()
      const currentTask = taskStore.getCurrentTask()
      const taskAgentId = currentTask?.agentId || leadId
      let sid = currentTask?.sessionId

      if (!sid) {
        sid = (await createSession({
          id: crypto.randomUUID(),
          scene_mode: globalSceneMode,
          workspace: leadWorkspace,
          model: leadModel,
          mode: leadInputMode,
          client_tools: CLIENT_TOOLS,
          agent_id: taskAgentId,
          agent_type: 'team'
        })).id
      }

      // Always sync the resolved session ID into the multi-agent store so plan/tool
      // action methods (confirmTool, skipTool, etc.) can find it.
      ma.setSessionId(sid)

      await sendChatMessage({
        sessionId: sid,
        content: text.trim(),
        mode: leadInputMode,
        sceneMode: globalSceneMode,
        workspace: leadWorkspace,
        model: leadModel,
        files,
        skillInvocations,
        agentId: taskAgentId,
        agentType: 'team',
        onEvent: (event) => {
          ma.handleStreamEvent(event)
        },
        onError: (err) => {
          ma.addMessage(leadId, {
            agentId: leadId,
            role: 'agent',
            content: `**错误:** ${err.message}`,
            type: 'text'
          })
          ma.updateAgentStatus(leadId, 'done')
          ma.setProcessing(false)
        },
        onDone: () => {
          ma.setProcessing(false)
        }
      })
    } catch (err: any) {
      ma.addMessage(leadId, {
        agentId: leadId,
        role: 'agent',
        content: `**错误:** ${err?.message || String(err)}`,
        type: 'text'
      })
      ma.updateAgentStatus(leadId, 'done')
      ma.setProcessing(false)
    }
  }, [leadWorkspace, leadModel, leadInputMode, globalSceneMode])

  const isProcessing = session.isProcessing

  return (
    <div className="flex-1 flex flex-col bg-[#e2e8f0] min-h-0">
      {/* Header */}
      <div className="flex items-center gap-3.5 px-5 py-3.5 bg-white border-b border-[#e2e8f0] flex-shrink-0">
        <span className="text-[22px]">{session.teamIcon}</span>
        <h3 className="text-base font-semibold text-[#0f172a] tracking-[-0.2px] flex-1">
          {session.teamName} · {session.agents.length} 名成员
        </h3>
      </div>

      {/* Agent Tabs */}
      <div className="flex gap-0 px-4 bg-white border-b border-[#e2e8f0] flex-shrink-0 overflow-x-auto">
        {session.agents.map(a => (
          <div
            key={a.id}
            className={`flex items-center gap-2.5 py-2.5 px-4 border-b-2 cursor-pointer transition-colors text-[13px] font-medium whitespace-nowrap flex-shrink-0 ${!a._hidden
              ? 'border-[#a7f3d0] text-[#0f172a] font-semibold'
              : 'border-transparent text-[#64748b] hover:text-[#0f172a] hover:bg-[#f8fafc]'
              }`}
            onClick={() => a._hidden && toggleColumn(a.id)}
          >
            <div
              className="w-7 h-7 rounded-full flex items-center justify-center text-[13px] font-semibold text-white flex-shrink-0"
              style={{ backgroundColor: a.role === 'lead' ? '#10b981' : '#94a3b8' }}
            >
              {a.avatar}
            </div>
            <div className="flex flex-col">
              <span className="font-medium text-[13px] leading-[1.2]">{a.displayName}</span>
              <span className="text-[10px] text-[#94a3b8] leading-[1.2]">{a.profession}</span>
            </div>
            <StatusDot status={a.status} />
            {!a._hidden && (
              <button
                onClick={(e) => { e.stopPropagation(); toggleColumn(a.id) }}
                className="w-5 h-5 rounded border-0 bg-transparent cursor-pointer text-[#94a3b8] text-sm flex items-center justify-center hover:bg-[#e2e8f0] hover:text-[#0f172a] transition-colors flex-shrink-0 ml-0.5"
                title="折叠列"
              >
                ×
              </button>
            )}
          </div>
        ))}
        {hasHidden && (
          <button
            onClick={showAllColumns}
            className="flex items-center gap-1 py-1.5 px-3 rounded-md border border-dashed border-[#e2e8f0] cursor-pointer text-[11px] font-medium text-[#94a3b8] bg-transparent hover:border-[#a7f3d0] hover:text-[#047857] hover:bg-[#f0fdf4] transition-colors whitespace-nowrap flex-shrink-0 my-2 ml-1"
            disabled={!hasHidden}
          >
            <svg className="w-3 h-3" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="2"><path d="M3 8h10M8 3v10" /></svg>
            显示全部
          </button>
        )}
      </div>

      {/* Columns */}
      <div className="flex-1 flex min-h-0 relative">
        {isScrollable && (
          <>
            <button
              className={`absolute top-1/2 -translate-y-1/2 w-7 h-12 rounded-md bg-[rgba(15,23,42,0.45)] text-white z-30 text-base flex items-center justify-center cursor-pointer transition-opacity ${showLeftHint ? 'opacity-100 pointer-events-auto' : 'opacity-0 pointer-events-none'}`}
              style={{ left: 4 }}
              onClick={() => scrollColumns('left')}
            >
              ‹
            </button>
            <button
              className={`absolute top-1/2 -translate-y-1/2 w-7 h-12 rounded-md bg-[rgba(15,23,42,0.45)] text-white z-30 text-base flex items-center justify-center cursor-pointer transition-opacity ${showRightHint ? 'opacity-100 pointer-events-auto' : 'opacity-0 pointer-events-none'}`}
              style={{ right: 4 }}
              onClick={() => scrollColumns('right')}
            >
              ›
            </button>
          </>
        )}

        <div
          ref={scrollRef}
          className={`flex gap-2 p-2 flex-1 ${isScrollable ? 'overflow-x-auto' : ''}`}
        >
          {visibleAgents.map(a => (
            <AgentColumnView
              key={a.id}
              agent={a}
              isLead={a.id === session.leadAgentId}
              onLeadSend={handleLeadSend}
              isProcessing={isProcessing}
              statusText={STATUS_TEXT}
              numCols={numCols}
              isScrollable={isScrollable}
              leadWorkspace={leadWorkspace}
              onLeadWorkspaceChange={setLeadWorkspace}
              leadModel={leadModel}
              onLeadModelChange={setLeadModel}
              leadInputMode={leadInputMode}
              onLeadInputModeChange={setLeadInputMode}
            />
          ))}
        </div>
      </div>
    </div>
  )
}

const StatusDot = memo(function StatusDot({ status }: { status: AgentColumn['status'] }) {
  const colorMap: Record<string, string> = {
    idle: '#cbd5e1',
    thinking: '#f59e0b',
    running: '#3b82f6',
    done: '#10b981',
    waiting: '#8b5cf6',
    failed: '#ef4444'
  }
  // waiting 也让它闪：父挂起等子时界面必须看得出"还活着"，否则像卡死。
  const animClass =
    status === 'thinking' || status === 'running' || status === 'waiting' ? 'animate-pulse' : ''

  return (
    <span
      className={`w-[7px] h-[7px] rounded-full flex-shrink-0 ml-0.5 ${animClass}`}
      style={{ backgroundColor: colorMap[status] || '#cbd5e1' }}
    />
  )
})

const AgentColumnView = memo(function AgentColumnView({
  agent, isLead, onLeadSend, isProcessing, statusText, numCols, isScrollable,
  leadWorkspace, onLeadWorkspaceChange, leadModel, onLeadModelChange, leadInputMode, onLeadInputModeChange
}: {
  agent: AgentColumn
  isLead: boolean
  onLeadSend: (text: string, files?: string[], skillInvocations?: { skill_id: string; skill_name: string }[]) => void
  isProcessing: boolean
  statusText: Record<string, string>
  numCols: number
  isScrollable: boolean
  leadWorkspace: string
  onLeadWorkspaceChange: (path: string) => void
  leadModel: string
  onLeadModelChange: (model: string) => void
  leadInputMode: AppMode
  onLeadInputModeChange: (mode: AppMode) => void
}) {
  const isLeadCol = isLead

  const flexBasis = isScrollable
    ? isLeadCol ? '0 0 380px' : '0 0 280px'
    : numCols === 2
      ? isLeadCol ? '1 1 55%' : '1 1 45%'
      : numCols === 3
        ? isLeadCol ? '1 1 50%' : '1 1 25%'
        : '1 1 25%'

  const leadFlex = isLeadCol && numCols >= 4 ? '1 1 36%' : undefined

  const messageCount = agent.messages.length
  const segmentCount = agent.messages.reduce((sum, m) => sum + (m.segments?.length ?? 0), 0)
  const isStreaming = agent.messages.some(m => m.isStreaming) ?? false
  const { scrollRef, contentRef } = useAutoScroll(messageCount, segmentCount, isStreaming)

  return (
    <div
      className={`flex flex-col bg-white border border-[#e2e8f0] rounded-[10px] overflow-hidden min-w-0 ${isLeadCol ? 'shadow-[0_0_0_2px_rgba(167,243,208,0.25)] border-[#a7f3d0]' : ''
        }`}
      style={{ flex: isScrollable ? flexBasis : (leadFlex || flexBasis) }}
    >
      {/* Col Header */}
      <div className="flex items-center gap-2 py-2.5 px-3.5 border-b border-[#f1f5f9] flex-shrink-0 bg-[#fafbfc]">
        <div
          className="w-6 h-6 rounded-full flex items-center justify-center text-[11px] font-semibold text-white flex-shrink-0"
          style={{ backgroundColor: agent.role === 'lead' ? '#10b981' : '#94a3b8' }}
        >
          {agent.avatar}
        </div>
        <span className="font-semibold text-xs text-[#0f172a]">{agent.displayName}</span>
        <span className={`text-[10px] py-0.5 px-1.5 rounded-lg font-semibold ml-auto flex-shrink-0 ${agent.role === 'lead' ? 'bg-[#ecfdf5] text-[#047857]' : 'bg-[#f1f5f9] text-[#64748b]'
          }`}>
          {agent.role === 'lead' ? '领队' : '成员'}
        </span>
      </div>

      {/* Col Config Bar */}
      <div className="flex items-center gap-1 py-1 px-2 border-b border-[#f1f5f9] flex-shrink-0 bg-[#fafbfc] overflow-x-auto">
        <div className="flex items-center gap-0.5 py-0.5 px-1.5 border border-[#e2e8f0] rounded text-[10px] text-[#64748b] bg-white cursor-pointer whitespace-nowrap flex-shrink-0">
          <span className="text-xs mr-0.5">🔧</span>
          <span>max_turn {agent.config.max_turn}</span>
        </div>
        <div className="flex items-center gap-0.5 py-0.5 px-1.5 border border-[#e2e8f0] rounded text-[10px] text-[#64748b] bg-white cursor-pointer whitespace-nowrap flex-shrink-0">
          <span className="text-xs mr-0.5">⏱️</span>
          <span>{agent.config.timeout_seconds}s</span>
        </div>
      </div>

      {/* Col Messages */}
      <div ref={scrollRef} className="flex-1 overflow-y-auto p-3" style={{ scrollbarWidth: 'none' }}>
        <div ref={contentRef} className="flex flex-col gap-2.5">
          {agent.messages.length === 0 && (
            <div className="text-[11px] text-[#94a3b8] text-center py-6">
              {isLeadCol ? '输入任务，委派给专家团队...' : '等待领队分配任务...'}
            </div>
          )}
          {agent.messages.map(msg => (
            <AgentMessageItem key={msg.id} msg={msg} />
          ))}
        </div>
      </div>

      {/* Col Input (lead) or Status Footer (member) */}
      {isLeadCol ? (
        <div className="border-t border-[#f1f5f9] flex-shrink-0 bg-[#fafbfc]">
          <ChatInput
            compact
            onSend={(text, files, skills) => onLeadSend(text, files, skills)}
            isProcessing={isProcessing}
            placeholder="输入任务，委派给专家团队..."
            workspacePath={leadWorkspace}
            onWorkspaceChange={onLeadWorkspaceChange}
            model={leadModel}
            onModelChange={onLeadModelChange}
            inputMode={leadInputMode}
            onInputModeChange={onLeadInputModeChange}
          />
        </div>
      ) : (
        <div className={`p-2.5 border-t border-[#f1f5f9] flex items-center gap-2 text-[11px] flex-shrink-0 bg-[#fafbfc] font-medium ${agent.status === 'thinking' ? 'text-[#b45309]' :
          agent.status === 'running' ? 'text-[#3b82f6]' :
            agent.status === 'done' ? 'text-[#047857]' :
              agent.status === 'waiting' ? 'text-[#8b5cf6]' :
                agent.status === 'failed' ? 'text-[#ef4444]' :
                  'text-[#94a3b8]'
          }`}>
          <StatusDot status={agent.status} />
          <span className="flex-1">{statusText[agent.status]}</span>
        </div>
      )}
    </div>
  )
})

const AgentMessageItem = memo(function AgentMessageItem({ msg }: { msg: MultiAgentMessage }) {
  const confirmPlan = useMultiAgentStore((s) => s.confirmPlan)
  const rejectPlan = useMultiAgentStore((s) => s.rejectPlan)
  const answerPlanQuestion = useMultiAgentStore((s) => s.answerPlanQuestion)
  const confirmTool = useMultiAgentStore((s) => s.confirmTool)
  const skipTool = useMultiAgentStore((s) => s.skipTool)
  const stopTools = useMultiAgentStore((s) => s.stopTools)

  const [selectedPlanValue, setSelectedPlanValue] = useState<string | null>(null)
  const [planTextAnswer, setPlanTextAnswer] = useState('')
  const [processCollapsed, setProcessCollapsed] = useState(false)
  const [outputExpanded, setOutputExpanded] = useState(false)

  if (msg.type === 'delegation' && msg.delegation) {
    const d = msg.delegation
    const preview = d.outputPreview || ''
    const fullOutput = d.outputFull || ''
    // 折叠态那行是服务端砍到 200 字的预览；只有拿到更长的全文时才给展开开关，
    // 否则短产出下面挂个点不动的按钮只是噪音。
    const canExpand = fullOutput.length > preview.length
    return (
      <div className="my-1 p-2.5 bg-[#fefce8] border border-[#fde68a] rounded-md border-l-[3px] border-l-[#f59e0b] text-xs">
        <div className="text-[10px] font-semibold text-[#92400e] uppercase tracking-[0.3px] mb-1.5">委派任务</div>
        <div className="text-xs font-medium text-[#0f172a] mb-1.5">→ {d.to}</div>
        <div className="text-[11px] text-[#64748b] italic p-1.5 bg-white rounded">{d.prompt}</div>
        <div className={`text-[11px] font-medium mt-1.5 ${d.status === 'running' ? 'text-[#b45309]' :
          d.status === 'done' ? 'text-[#047857]' :
            'text-[#94a3b8]'
          }`}>
          {d.status === 'waiting' ? '等待中' : d.status === 'running' ? '执行中...' : d.status === 'done' ? '已完成' : d.status}
        </div>
        <div className={`text-xs font-medium ${canExpand ? '' : 'mb-1.5'} ${outputExpanded ? 'whitespace-pre-wrap' : ''}`}>
          {outputExpanded && fullOutput ? fullOutput : preview}
          {canExpand && !outputExpanded && '…'}
        </div>
        {canExpand && (
          <button
            type="button"
            onClick={() => setOutputExpanded((v) => !v)}
            className="mt-1 text-[11px] text-[#92400e] underline underline-offset-2 hover:text-[#78350f]"
          >
            {outputExpanded ? '收起' : '展开全文'}
          </button>
        )}
      </div>
    )
  }

  const isUser = msg.role === 'user'
  const agentId = msg.agentId
  const hasSegments = (msg.segments && msg.segments.length > 0)

  return (
    <div className={`flex flex-col gap-1 max-w-full ${isUser ? 'items-end' : ''}`}>
      {!isUser && (
        <span className="text-[10px] font-semibold text-[#94a3b8] uppercase tracking-[0.3px]">{agentId}</span>
      )}

      {hasSegments ? (
        <div className="p-2 px-3 rounded-[10px] bg-[#f8fafc] border border-[#f1f5f9] text-[#0f172a] text-xs leading-[1.55] min-w-0 max-w-full rounded-bl-[3px]">
          <SegmentsView
            segments={msg.segments || []}
            tools={msg.tools || []}
            msgIndex={0}
            isStreaming={msg.isStreaming ?? false}
            streamingText={msg.isStreaming ?? false}
            selectedPlanValue={selectedPlanValue}
            onSelectValue={setSelectedPlanValue}
            textAnswer={planTextAnswer}
            onTextAnswer={setPlanTextAnswer}
            onConfirmPlan={() => confirmPlan(agentId)}
            onEditPlan={() => { }}
            onRejectPlan={() => rejectPlan(agentId)}
            onSelectOption={() => { }}
            onSubmitAnswer={(_, answer) => answerPlanQuestion(agentId, answer)}
            planStatus={msg.planStatus}
            planEditing={msg.planEditing}
            processCollapsed={processCollapsed}
            onToggleCollapse={() => setProcessCollapsed(!processCollapsed)}
            onConfirmTool={(toolId) => confirmTool(agentId, toolId)}
            onSkipTool={(toolId) => skipTool(agentId, toolId)}
            onStopTools={() => stopTools(agentId)}
          />
        </div>
      ) : (
        <div className={`p-2 px-3 rounded-[10px] text-xs leading-[1.55] font-normal whitespace-pre-wrap break-words ${isUser
          ? 'bg-[#ecfdf5] text-[#064e3b] rounded-br-[3px]'
          : 'bg-[#f8fafc] border border-[#f1f5f9] text-[#0f172a] rounded-bl-[3px]'
          }`}>
          {msg.content}
        </div>
      )}
    </div>
  )
})
