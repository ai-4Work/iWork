import { useState, useEffect, type ReactNode } from 'react'
import { useConfigStore } from '../../stores/configStore'
import type { L1MemoryItem, L2SceneItem, L3PersonaItem, RuleItem } from '../../types'
import { useCardTooltip } from './useCardTooltip'

const L1_TYPE_LABELS: Record<string, string> = {
  persona: '画像',
  episodic: '事件',
  instruction: '指令'
}

/** 只上文字色、不填彩底：左侧色条已经在标类型，再填一个彩底等于同一信息说两遍。 */
const L1_TYPE_COLORS: Record<string, string> = {
  persona: 'text-blue-600',
  episodic: 'text-emerald-600',
  instruction: 'text-amber-600'
}

/** 卡片左侧 3px 色条，按类型区分。 */
const L1_TYPE_ACCENTS: Record<string, string> = {
  persona: '#93c5fd',
  episodic: '#6ee7b7',
  instruction: '#fcd34d'
}

const PERSONA_ACCENT = '#a5b4fc'
const RULE_ACCENT = '#a7f3d0'
const NEUTRAL_ACCENT = '#e2e8f0'

/** 按场景聚合：一个场景下挂多条原子记忆，正是 L1 → L2 的聚合关系。组内按重要度降序。 */
function groupByScene(memories: L1MemoryItem[]): { scene: string; items: L1MemoryItem[] }[] {
  const groups = new Map<string, L1MemoryItem[]>()
  for (const m of memories) {
    const scene = m.scene_name || '（无场景）'
    if (!groups.has(scene)) groups.set(scene, [])
    groups.get(scene)!.push(m)
  }
  return [...groups.entries()].map(([scene, items]) => ({
    scene,
    items: [...items].sort((a, b) => b.priority - a.priority)
  }))
}

/** 热度分档，与服务端 heat_flames 同口径（doc L2-3.3）。 */
const HEAT_TIERS: [number, number][] = [[1000, 5], [500, 4], [200, 3], [100, 2], [50, 1]]

function heatFlames(heat: number): number {
  for (const [threshold, flames] of HEAT_TIERS) {
    if (heat >= threshold) return flames
  }
  return 0
}

function heatText(heat: number): string {
  const n = heatFlames(heat)
  return n > 0 ? '🔥'.repeat(n) : '·'
}

function heatAccent(heat: number): string {
  const n = heatFlames(heat)
  return n >= 4 ? '#fca5a5' : n >= 2 ? '#fdba74' : NEUTRAL_ACCENT
}

function formatDate(iso: string): string {
  if (!iso) return ''
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return iso
  const pad = (v: number) => String(v).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`
}

/** 活动时间只留一个日期点：区间两端同一天也只显示一个，不带时区与时刻。 */
function activityTime(m: L1MemoryItem): string {
  const raw = m.activity_start_time || m.activity_end_time
  if (!raw) return ''
  const d = new Date(raw)
  if (Number.isNaN(d.getTime())) return raw.slice(0, 10)
  const pad = (v: number) => String(v).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`
}

/** 四段共用卡片壳：左侧色条分类、极淡静置阴影、悬停浮起。一行一个长条。 */
const CARD =
  'bg-white border border-[#e2e8f0] border-l-[3px] rounded-[10px] px-3 py-2 shadow-[0_1px_2px_rgba(15,23,42,0.04)] ' +
  'hover:shadow-[0_2px_8px_rgba(15,23,42,0.08)] hover:border-[#cbd5e1] transition-all group'

/** 长条的第一行：分类在左、主信息占中、元信息靠右。 */
const STRIP = 'flex items-center gap-3'

const ICON_BTN =
  'p-1 rounded transition-colors bg-transparent border-none cursor-pointer flex-shrink-0 opacity-0 group-hover:opacity-100'

const CHIP = 'text-[10px] px-1.5 py-px rounded-full font-medium flex-shrink-0'
const TIME_TEXT = 'text-[10px] text-[#94a3b8] flex-shrink-0'

const TrashIcon = () => (
  <svg width="12" height="12" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><polyline points="2 4 14 4 12 16 4 16 2 4" /><line x1="6" y1="7" x2="6" y2="12" /><line x1="10" y1="7" x2="10" y2="12" /></svg>
)

/** 折叠段：标题行常驻（带条数与一句话说明），展开才拉出内容。交互沿用 SegmentsView 的「思考过程」块。 */
function CollapsibleSection({
  title, meta, note, actions, open, onToggle, children
}: {
  title: string
  meta: string
  note?: string
  actions?: ReactNode
  open: boolean
  onToggle: () => void
  children: ReactNode
}) {
  return (
    <div className="border border-[#e2e8f0] rounded-lg overflow-hidden mb-3">
      <div
        onClick={onToggle}
        className="flex items-center gap-2 px-3 py-2.5 cursor-pointer bg-[#f8fafc] select-none hover:bg-[#f1f5f9] transition-colors"
      >
        <span className={`inline-block transition-transform text-[10px] text-[#94a3b8] ${open ? '' : '-rotate-90'}`}>▼</span>
        <span className="text-[12px] font-semibold text-[#475569] flex-shrink-0">{title}</span>
        <span className="text-[11px] text-[#94a3b8] flex-shrink-0">{meta}</span>
        {note && (
          <span className="flex-1 min-w-0 truncate text-[11px] text-[#94a3b8]" title={note}>{note}</span>
        )}
        <div className={`${note ? '' : 'ml-auto '}flex items-center gap-1`} onClick={e => e.stopPropagation()}>{actions}</div>
      </div>
      {open && <div className="border-t border-[#f1f5f9] bg-[#f8fafc] p-3">{children}</div>}
    </div>
  )
}

function RefreshButton({ loading, onClick }: { loading: boolean; onClick: () => void }) {
  return (
    <button
      onClick={onClick}
      disabled={loading}
      className="flex items-center gap-1 px-2 py-0.5 rounded text-[11px] font-medium text-[#64748b] bg-white border border-[#e2e8f0] hover:border-[#cbd5e1] transition-colors cursor-pointer disabled:opacity-50"
    >
      <svg className={`w-3 h-3 ${loading ? 'animate-spin' : ''}`} viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round"><path d="M13.5 8a5.5 5.5 0 1 1-1.6-3.9" /><path d="M13.5 2v3h-3" /></svg>
      刷新
    </button>
  )
}

const Empty = ({ children }: { children: ReactNode }) => (
  <div className="text-[12px] text-[#94a3b8] py-8 text-center">{children}</div>
)

const ErrorText = ({ children }: { children: ReactNode }) => (
  <div className="text-[12px] text-[#ef4444] py-8 text-center">{children}</div>
)

export function MemoryConfig() {
  const {
    l1Memories, l1MemoriesLoading, l1MemoriesError,
    l2Scenes, l2ScenesLoading, l2ScenesError,
    l3Personas, l3PersonasLoading, l3PersonasError,
    rules,
    loadL1Memories, deleteL1MemoryAction,
    loadL2Scenes, deleteL2SceneAction,
    loadL3Personas, deleteL3PersonaAction,
    loadRules, saveRuleAction, deleteRuleAction
  } = useConfigStore()

  const { hoverProps, tooltip } = useCardTooltip()

  const [openSections, setOpenSections] = useState({ l1: false, l2: false, l3: false, rules: false })
  const [expandedMemory, setExpandedMemory] = useState<string | null>(null)
  const [expandedScene, setExpandedScene] = useState<string | null>(null)
  const [expandedPersona, setExpandedPersona] = useState<string | null>(null)
  const [showModal, setShowModal] = useState(false)
  const [editingRule, setEditingRule] = useState<RuleItem | null>(null)
  const [submitting, setSubmitting] = useState(false)

  const [formName, setFormName] = useState('')
  const [formDesc, setFormDesc] = useState('')
  const [formContent, setFormContent] = useState('')
  const [formPriority, setFormPriority] = useState(0)

  useEffect(() => {
    loadL1Memories()
    loadL2Scenes()
    loadL3Personas()
    loadRules()
  }, [])

  const toggleSection = (key: 'l1' | 'l2' | 'l3' | 'rules') =>
    setOpenSections(s => ({ ...s, [key]: !s[key] }))

  // ── L1 actions ──

  const handleDeleteMemory = async (item: L1MemoryItem) => {
    if (!confirm(`确定删除这条原子记忆？\n\n${item.content}`)) return
    try {
      await deleteL1MemoryAction(item.id)
    } catch (err: any) {
      alert(err.message || '删除失败')
    }
  }

  // ── L2 actions ──

  const handleDeleteScene = async (item: L2SceneItem) => {
    if (!confirm(`确定删除场景 "${item.name}"？\n\n它会连同聚合进它的历史一起消失，且不再被注入。`)) return
    try {
      await deleteL2SceneAction(item.id)
    } catch (err: any) {
      alert(err.message || '删除失败')
    }
  }

  // ── L3 actions ──

  const handleDeletePersona = async (item: L3PersonaItem) => {
    const scope = item.agent_id || '顶层'
    if (!confirm(`确定删除作用域 "${scope}" 的画像？\n\n删除后下次记忆整合会重新生成一份。`)) return
    try {
      await deleteL3PersonaAction(item.agent_id)
    } catch (err: any) {
      alert(err.message || '删除失败')
    }
  }

  // ── Rule actions ──

  const openNewRule = () => {
    setEditingRule(null)
    setFormName('')
    setFormDesc('')
    setFormContent('')
    setFormPriority(0)
    setShowModal(true)
  }

  const openEditRule = (item: RuleItem) => {
    setEditingRule(item)
    setFormName(item.name)
    setFormDesc(item.description)
    setFormContent(item.content)
    setFormPriority(item.priority)
    setShowModal(true)
  }

  const handleDeleteRule = async (item: RuleItem) => {
    if (!confirm(`确定删除规则 "${item.name}"？`)) return
    try {
      await deleteRuleAction(item.id)
    } catch (err: any) {
      alert(err.message || '删除失败')
    }
  }

  const handleSubmitRule = async () => {
    if (!formName.trim() || !formContent.trim()) return
    setSubmitting(true)
    try {
      await saveRuleAction({
        name: formName.trim(),
        description: formDesc.trim(),
        content: formContent.trim(),
        priority: formPriority
      })
      setShowModal(false)
    } catch (err: any) {
      alert(err.message || '保存失败')
    } finally {
      setSubmitting(false)
    }
  }

  const isValid = formName.trim().length > 0 && formContent.trim().length >= 10
  const groups = groupByScene(l1Memories)

  return (
    <div>
      {/* ═══════════ L1 原子记忆（只读，随对话自动抽取）═══════════ */}
      <CollapsibleSection
        title="原子记忆"
        meta={`${l1Memories.length} 条`}
        note="服务端从对话里自动抽取，无需手工维护；可在下一轮对话中被召回。"
        open={openSections.l1}
        onToggle={() => toggleSection('l1')}
        actions={<RefreshButton loading={l1MemoriesLoading} onClick={() => loadL1Memories()} />}
      >
        {l1MemoriesError ? (
          <ErrorText>{l1MemoriesError}</ErrorText>
        ) : groups.length === 0 ? (
          <Empty>暂无原子记忆</Empty>
        ) : (
          <div className="flex flex-col gap-2.5">
            {groups.map(g => (
              <div key={g.scene} className="rounded-[10px] bg-[#edf1f6] border border-[#e2e8f0] overflow-hidden">
                {/* 底色只比段背景(#f8fafc)深一档，靠描边分块，不靠重底色；标题比块内卡片大一号 */}
                <div className="flex items-center gap-2 px-3 py-2">
                  <span className="text-[13px] font-semibold text-[#0f172a] truncate">{g.scene}</span>
                  <span className="text-[10px] text-[#64748b] flex-shrink-0">{g.items.length} 条</span>
                </div>
                {/* 块内多条紧贴成一份清单，只在条与条之间留一条细分隔线 */}
                <div className="mx-2 mb-2 rounded-md bg-white overflow-hidden">
                  {g.items.map((m, i) => {
                    const time = activityTime(m)
                    const expanded = expandedMemory === m.id
                    return (
                      <div
                        key={m.id}
                        onClick={() => setExpandedMemory(expanded ? null : m.id)}
                        className={`group px-3 py-2 border-l-[3px] cursor-pointer transition-colors hover:bg-[#f8fafc] ${
                          i > 0 ? 'border-t border-[#f1f5f9]' : ''
                        }`}
                        style={{ borderLeftColor: L1_TYPE_ACCENTS[m.type] || NEUTRAL_ACCENT }}
                        {...hoverProps(m.content, time ? `活动时间：${time}` : '')}
                      >
                        <div className={STRIP}>
                          <span className={`text-[11px] font-medium flex-shrink-0 ${L1_TYPE_COLORS[m.type] || 'text-[#64748b]'}`}>
                            {L1_TYPE_LABELS[m.type] || m.type}
                          </span>
                          {/* 展开时正文挪到下方，顶部只留一条空的占位，避免同一句话出现两遍 */}
                          {expanded ? (
                            <span className="flex-1" />
                          ) : (
                            <span className="flex-1 min-w-0 truncate text-[12px] text-[#334155]" data-tip-name>
                              {m.content}
                            </span>
                          )}
                          <span className={TIME_TEXT}>重要度 {m.priority}</span>
                          {m.version > 1 && (
                            <span className={`${CHIP} bg-[#f1f5f9] text-[#64748b]`} title="该记忆被更新过">v{m.version}</span>
                          )}
                          {time && <span className={TIME_TEXT}>{time}</span>}
                          <button
                            onClick={e => {
                              e.stopPropagation()
                              handleDeleteMemory(m)
                            }}
                            className={`${ICON_BTN} text-[#94a3b8] hover:text-[#ef4444] hover:bg-[#fef2f2]`}
                            title="删除这条记忆"
                          >
                            <TrashIcon />
                          </button>
                        </div>
                        {expanded && (
                          <div className="mt-2 rounded-md bg-[#f8fafc] border border-[#f1f5f9] px-3 py-2.5 text-[12px] text-[#334155] whitespace-pre-wrap break-words leading-relaxed max-h-[320px] overflow-y-auto">
                            {m.content}
                          </div>
                        )}
                      </div>
                    )
                  })}
                </div>
              </div>
            ))}
          </div>
        )}
      </CollapsibleSection>

      {/* ═══════════ L2 场景记忆（只读，跨会话整合）═══════════ */}
      <CollapsibleSection
        title="场景记忆"
        meta={`${l2Scenes.length} 个场景`}
        note="服务端把原子记忆整合成跨会话的场景叙事；场景导航会注入提示词，模型可按名字读取全文。"
        open={openSections.l2}
        onToggle={() => toggleSection('l2')}
        actions={<RefreshButton loading={l2ScenesLoading} onClick={() => loadL2Scenes()} />}
      >
        {l2ScenesError ? (
          <ErrorText>{l2ScenesError}</ErrorText>
        ) : l2Scenes.length === 0 ? (
          <Empty>暂无场景记忆</Empty>
        ) : (
          <div className="flex flex-col gap-1.5">
            {l2Scenes.map(s => {
              const flames = heatFlames(s.heat)
              const expanded = expandedScene === s.id
              return (
                <div
                  key={s.id}
                  onClick={() => setExpandedScene(expanded ? null : s.id)}
                  className={`${CARD} cursor-pointer`}
                  style={{ borderLeftColor: heatAccent(s.heat) }}
                >
                  <div className={STRIP}>
                    <span className="text-[13px] font-medium text-[#0f172a] truncate max-w-[220px] min-w-0">{s.name}</span>
                    {s.version > 1 && (
                      <span className={`${CHIP} bg-[#f1f5f9] text-[#64748b]`} title="该场景被更新过">v{s.version}</span>
                    )}
                    {s.agent_id && s.agent_id !== '/root' && (
                      <span className={`${CHIP} bg-[#f0fdf4] text-[#047857]`}>{s.agent_id}</span>
                    )}
                    {!expanded && s.summary
                      ? <span className="flex-1 min-w-0 truncate text-[11px] text-[#94a3b8]">{s.summary}</span>
                      : <span className="flex-1" />}
                    <span
                      className={`${CHIP} ${flames >= 4 ? 'bg-[#fef2f2] text-[#ef4444]'
                        : flames >= 2 ? 'bg-[#fff7ed] text-[#f97316]'
                          : 'bg-[#f1f5f9] text-[#64748b]'}`}
                      title={`热度 ${s.heat}`}
                    >
                      {flames > 0 ? `${heatText(s.heat)} ${s.heat}` : `${heatText(s.heat)} 冷`}
                    </span>
                    <span className={TIME_TEXT}>{formatDate(s.updated_at)}</span>
                    <button
                      onClick={e => { e.stopPropagation(); handleDeleteScene(s) }}
                      className={`${ICON_BTN} text-[#94a3b8] hover:text-[#ef4444] hover:bg-[#fef2f2]`}
                      title="删除这个场景"
                    >
                      <TrashIcon />
                    </button>
                  </div>

                  {expanded && (
                    <div className="mt-2 rounded-md bg-[#f8fafc] border border-[#f1f5f9] px-3 py-2.5 text-[12px] text-[#334155] whitespace-pre-wrap break-words leading-relaxed max-h-[320px] overflow-y-auto">
                      {s.content}
                    </div>
                  )}
                </div>
              )
            })}
          </div>
        )}
      </CollapsibleSection>

      {/* ═══════════ L3 画像记忆（只读，一个作用域一行）═══════════ */}
      <CollapsibleSection
        title="画像记忆"
        meta={`${l3Personas.length} 个作用域`}
        note="服务端把场景叙事综合成一份画像文档，每条消息前整份注入系统提示词。它是自动产物，只读、仅可删。"
        open={openSections.l3}
        onToggle={() => toggleSection('l3')}
        actions={<RefreshButton loading={l3PersonasLoading} onClick={() => loadL3Personas()} />}
      >
        {l3PersonasError ? (
          <ErrorText>{l3PersonasError}</ErrorText>
        ) : l3Personas.length === 0 ? (
          <Empty>暂无画像记忆</Empty>
        ) : (
          <div className="flex flex-col gap-1.5">
            {l3Personas.map(p => {
              const scope = p.agent_id || '顶层'
              const expanded = expandedPersona === p.agent_id
              return (
                <div
                  key={p.agent_id}
                  onClick={() => setExpandedPersona(expanded ? null : p.agent_id)}
                  className={`${CARD} cursor-pointer`}
                  style={{ borderLeftColor: PERSONA_ACCENT }}
                >
                  <div className={STRIP}>
                    <span className="text-[13px] font-medium text-[#0f172a] truncate max-w-[220px] min-w-0">{scope}</span>
                    <span className={`${CHIP} bg-[#eff6ff] text-[#2563eb]`} title="该画像被重写过几次">v{p.version}</span>
                    <span className={`${CHIP} bg-[#f1f5f9] text-[#64748b]`} title="生成这份画像时的原子记忆总数">
                      基于 {p.memory_count_at_generation} 条记忆
                    </span>
                    <span className="flex-1" />
                    <span className={TIME_TEXT}>{formatDate(p.updated_at)}</span>
                    <button
                      onClick={e => { e.stopPropagation(); handleDeletePersona(p) }}
                      className={`${ICON_BTN} text-[#94a3b8] hover:text-[#ef4444] hover:bg-[#fef2f2]`}
                      title="删除这个作用域的画像"
                    >
                      <TrashIcon />
                    </button>
                  </div>

                  {expanded && (
                    <div className="mt-2 rounded-md bg-[#f8fafc] border border-[#f1f5f9] px-3 py-2.5 text-[12px] text-[#334155] whitespace-pre-wrap break-words leading-relaxed max-h-[320px] overflow-y-auto">
                      {p.content}
                    </div>
                  )}
                </div>
              )
            })}
          </div>
        )}
      </CollapsibleSection>

      {/* ═══════════ Rules ═══════════ */}
      <CollapsibleSection
        title="Rules"
        meta={`${rules.length} 条`}
        open={openSections.rules}
        onToggle={() => toggleSection('rules')}
        actions={
          <button
            onClick={openNewRule}
            className="flex items-center gap-1 px-2 py-0.5 rounded text-[11px] font-medium text-[#047857] bg-[#f0fdf4] border border-[#a7f3d0] hover:bg-[#a7f3d0] transition-colors cursor-pointer"
          >
            <svg className="w-3 h-3" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round"><path d="M8 3v10M3 8h10" /></svg>
            新建
          </button>
        }
      >
        {rules.length === 0 ? (
          <Empty>暂无规则</Empty>
        ) : (
          <div className="flex flex-col gap-1.5">
            {rules.map(r => (
              <div
                key={r.id}
                className={CARD}
                style={{ borderLeftColor: RULE_ACCENT }}
                {...hoverProps(r.name, r.description)}
              >
                <div className={STRIP}>
                  <span className="text-[13px] font-medium text-[#0f172a] truncate max-w-[220px] min-w-0" data-tip-name>
                    {r.name}
                  </span>
                  <span className={`${CHIP} bg-indigo-50 text-indigo-600`}>优先级 {r.priority}</span>
                  {r.description
                    ? <span className="flex-1 min-w-0 truncate text-[11px] text-[#94a3b8]">{r.description}</span>
                    : <span className="flex-1" />}
                  <button
                    onClick={() => openEditRule(r)}
                    className={`${ICON_BTN} text-[#94a3b8] hover:text-[#047857] hover:bg-[#f0fdf4]`}
                    title="编辑规则"
                  >
                    <svg width="12" height="12" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="2"><path d="M2 14h2l8-8-2-2-8 8v2z" /><path d="M12 3l2 2" /></svg>
                  </button>
                  <button
                    onClick={() => handleDeleteRule(r)}
                    className={`${ICON_BTN} text-[#94a3b8] hover:text-[#ef4444] hover:bg-[#fef2f2]`}
                    title="删除规则"
                  >
                    <TrashIcon />
                  </button>
                </div>
              </div>
            ))}
          </div>
        )}
      </CollapsibleSection>

      {/* ═══════════ Rule Modal ═══════════ */}
      {showModal && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/20" onClick={() => setShowModal(false)}>
          <div className="bg-white rounded-xl shadow-lg p-6 w-[520px] max-h-[85vh] overflow-y-auto" onClick={e => e.stopPropagation()}>
            <div className="flex items-center justify-between mb-4">
              <h3 className="text-[15px] font-semibold text-[#0f172a]">
                {editingRule ? '编辑规则' : '新建规则'}
              </h3>
              <button onClick={() => setShowModal(false)} className="text-[#94a3b8] hover:text-[#0f172a] cursor-pointer">
                <svg className="w-4 h-4" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="2"><path d="M4 4l8 8M12 4l-8 8" /></svg>
              </button>
            </div>

            <div className="space-y-3.5">
              <div>
                <label className="block text-[12px] font-medium text-[#64748b] mb-1">
                  文件名 * <span className="text-[#94a3b8] font-normal">（如 always-typescript.md）</span>
                </label>
                <input
                  className="w-full px-2.5 py-[7px] border border-[#e2e8f0] rounded-md text-[13px] outline-none focus:border-[#a7f3d0]"
                  placeholder="always-typescript.md"
                  value={formName}
                  onChange={e => setFormName(e.target.value)}
                />
              </div>

              <div>
                <label className="block text-[12px] font-medium text-[#64748b] mb-1">描述</label>
                <input
                  className="w-full px-2.5 py-[7px] border border-[#e2e8f0] rounded-md text-[13px] outline-none focus:border-[#a7f3d0]"
                  placeholder="一行描述，用于索引展示"
                  value={formDesc}
                  onChange={e => setFormDesc(e.target.value)}
                />
              </div>

              <div>
                <label className="block text-[12px] font-medium text-[#64748b] mb-1">优先级 * <span className="text-[#94a3b8] font-normal">（越高越靠前，默认 0）</span></label>
                <input
                  type="number"
                  className="w-full px-2.5 py-[7px] border border-[#e2e8f0] rounded-md text-[13px] outline-none focus:border-[#a7f3d0]"
                  value={formPriority}
                  onChange={e => setFormPriority(Number(e.target.value))}
                />
              </div>

              <div>
                <label className="block text-[12px] font-medium text-[#64748b] mb-1">
                  内容 * <span className="text-[#94a3b8] font-normal">（Markdown，最少 10 字符）</span>
                </label>
                <textarea
                  className="w-full px-2.5 py-[7px] border border-[#e2e8f0] rounded-md text-[13px] outline-none focus:border-[#a7f3d0] min-h-[140px] resize-y font-mono"
                  placeholder={'# Rule\n\n始终使用 TypeScript...'}
                  value={formContent}
                  onChange={e => setFormContent(e.target.value)}
                />
                <div className="text-[11px] text-[#94a3b8] mt-1">{formContent.length} 字符</div>
              </div>
            </div>

            <div className="flex justify-end gap-2 mt-5">
              <button onClick={() => setShowModal(false)} className="px-4 py-1.5 rounded-md text-[13px] border border-[#e2e8f0] text-[#64748b] cursor-pointer">取消</button>
              <button
                onClick={handleSubmitRule}
                disabled={!isValid || submitting}
                className={`px-4 py-1.5 rounded-md text-[13px] font-medium cursor-pointer border ${
                  isValid && !submitting
                    ? 'border-[#a7f3d0] text-[#047857] bg-[#f0fdf4] hover:bg-[#a7f3d0]'
                    : 'border-[#e2e8f0] text-[#cbd5e1] bg-[#f8fafc] cursor-not-allowed'
                }`}
              >
                {submitting ? '保存中...' : editingRule ? '保存' : '创建'}
              </button>
            </div>
          </div>
        </div>
      )}

      {tooltip}
    </div>
  )
}
