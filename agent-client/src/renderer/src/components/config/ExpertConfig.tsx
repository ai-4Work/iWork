import { useState, useEffect, useRef } from 'react'
import type { Expert, Team } from '../../types'
import { fetchExperts, fetchTeams, downloadExpert, downloadTeam } from '../../services/api'
import { useTaskStore } from '../../stores/taskStore'
import { useMultiAgentStore } from '../../stores/multiAgentStore'
import { showToast } from '../../utils/toast'

const MOCK_EXPERTS: Expert[] = []
const MOCK_TEAMS: Team[] = []

function getExpertName(e: Expert): string {
  return e.displayName || e.display_name || e.id
}
function getTeamName(t: Team): string {
  return t.displayName || t.display_name || t.id
}
function getTeamLead(t: Team): string {
  return t.leadDisplayName || t.lead_display_name || ''
}
function getTeamLeadProf(t: Team): string {
  return t.leadProfession || t.lead_profession || ''
}
function getMemberName(m: Team['members'][number]): string {
  if (m.displayName) return m.displayName
  if (m.name) return m.name.zh || m.name.en || m.id
  return m.id
}
function getMemberProfession(m: Team['members'][number]): string {
  if (typeof m.profession === 'string') return m.profession
  return (m.profession as any)?.zh || (m.profession as any)?.en || ''
}

export function ExpertConfig({ onClose }: { onClose: () => void }) {
  const [tab, setTab] = useState<'expert' | 'team'>('expert')
  const [search, setSearch] = useState('')
  const [selectedExpertId, setSelectedExpertId] = useState<string | null>(null)
  const [expandedTeamId, setExpandedTeamId] = useState<string | null>(null)

  const [experts, setExperts] = useState<Expert[]>(MOCK_EXPERTS)
  const [teams, setTeams] = useState<Team[]>(MOCK_TEAMS)
  const [loading, setLoading] = useState(false)
  const [installingId, setInstallingId] = useState<string | null>(null)
  const fetchingRef = useRef(false)

  const taskStore = useTaskStore()
  const multiAgentStore = useMultiAgentStore()

  useEffect(() => {
    if (fetchingRef.current) return
    fetchingRef.current = true
    setLoading(true)
    Promise.all([
      fetchExperts().catch(() => ({ experts: MOCK_EXPERTS })),
      fetchTeams().catch(() => ({ teams: MOCK_TEAMS }))
    ]).then(([expertData, teamData]) => {
      setExperts(expertData.experts.length > 0 ? expertData.experts : MOCK_EXPERTS)
      setTeams(teamData.teams.length > 0 ? teamData.teams : MOCK_TEAMS)
      setLoading(false)
    })
  }, [])

  const filteredExperts = experts.filter(e =>
    !search || getExpertName(e).includes(search) || e.profession.includes(search) ||
    e.desc.includes(search) || e.tags.some(t => t.includes(search))
  )
  const filteredTeams = teams.filter(t =>
    !search || getTeamName(t).includes(search) || t.desc.includes(search) ||
    getTeamLead(t).includes(search)
  )

  const handleUseExpert = async (id: string) => {
    const expert = experts.find(e => e.id === id)
    if (!expert) return

    setInstallingId(id)
    try {
      await downloadExpert(id)
    } catch (err: any) {
      showToast('下载失败：' + (err?.message || '未知错误'))
      setInstallingId(null)
      return
    }
    setInstallingId(null)

    await taskStore.create({
      agentId: id,
      agentType: 'expert',
      agentName: getExpertName(expert)
    })
    onClose()
  }

  const handleUseTeam = async (id: string) => {
    const team = teams.find(t => t.id === id)
    if (!team) return

    setInstallingId(id)
    try {
      await downloadTeam(id)
    } catch (err: any) {
      showToast('下载失败：' + (err?.message || '未知错误'))
      setInstallingId(null)
      return
    }
    setInstallingId(null)

    const taskId = await taskStore.create({
      agentId: id,
      agentType: 'team',
      agentName: getTeamName(team)
    })

    // Open multi-agent panel with team members + expert configs
    multiAgentStore.openSession(team, experts, taskId)
    onClose()
  }

  return (
    <div className="flex flex-col gap-0">
      {/* Tabs + Search */}
      <div className="flex items-center gap-2 mb-5">
        <div className="flex gap-0.5 bg-[#f1f5f9] rounded-md p-[3px]">
          <button
            onClick={() => { setTab('expert'); setSelectedExpertId(null); setExpandedTeamId(null) }}
            className={`px-[18px] py-1.5 rounded-[5px] text-[13px] font-medium tracking-[-0.1px] transition-colors ${tab === 'expert' ? 'bg-white text-[#0f172a] shadow-sm' : 'text-[#64748b] hover:text-[#0f172a]'}`}
          >
            <span className="text-base mr-1">👤</span> 专家 Hub
          </button>
          <button
            onClick={() => { setTab('team'); setSelectedExpertId(null); setExpandedTeamId(null) }}
            className={`px-[18px] py-1.5 rounded-[5px] text-[13px] font-medium tracking-[-0.1px] transition-colors ${tab === 'team' ? 'bg-white text-[#0f172a] shadow-sm' : 'text-[#64748b] hover:text-[#0f172a]'}`}
          >
            <span className="text-base mr-1">👥</span> 专家团 Hub
          </button>
        </div>
        <div className="relative ml-auto">
          <svg className="w-4 h-4 absolute left-2.5 top-1/2 -translate-y-1/2 text-[#94a3b8]" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round"><circle cx="7" cy="7" r="4.5" /><line x1="10.5" y1="10.5" x2="14" y2="14" /></svg>
          <input
            type="text"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder={tab === 'expert' ? '搜索专家...' : '搜索团队...'}
            className="w-[200px] py-1.5 pl-[30px] pr-2.5 border border-[#e2e8f0] rounded-md text-[13px] text-[#0f172a] bg-white outline-none placeholder:text-[#94a3b8] focus:border-[#a7f3d0] focus:shadow-[0_0_0_3px_rgba(167,243,208,0.15)] transition-colors"
          />
        </div>
      </div>

      {loading && (
        <div className="flex items-center gap-2 text-sm text-[#94a3b8] py-3">
          <svg className="w-4 h-4 animate-spin" viewBox="0 0 16 16" fill="none"><circle cx="8" cy="8" r="6" stroke="currentColor" strokeWidth="2" strokeDasharray="28" strokeDashoffset="8" /></svg>
          加载中...
        </div>
      )}

      {/* Expert Tab */}
      {tab === 'expert' && !loading && (
        <div className="grid gap-2.5" style={{ gridTemplateColumns: 'repeat(auto-fill, minmax(280px, 1fr))' }}>
          {filteredExperts.length > 0 ? filteredExperts.map(e => {
            const selected = selectedExpertId === e.id
            const isInstalling = installingId === e.id
            return (
              <div
                key={e.id}
                onClick={() => setSelectedExpertId(selectedExpertId === e.id ? null : e.id)}
                className={`bg-white border rounded-[10px] p-5 flex flex-col gap-3.5 relative transition-colors cursor-pointer group ${
                  selected
                    ? 'border-[#a7f3d0] border-l-[3px] border-l-[#a7f3d0] bg-[#f0fdf4]'
                    : 'border-[#e2e8f0] border-l-[3px] border-l-transparent hover:border-[#cbd5e1] hover:shadow-sm'
                }`}
              >
                {/* Use button */}
                <button
                  onClick={(ev) => { ev.stopPropagation(); handleUseExpert(e.id) }}
                  disabled={isInstalling}
                  className={`absolute top-3 right-4 items-center gap-1 px-3.5 py-1.5 rounded-md text-xs font-semibold text-white bg-[#1e293b] hover:bg-[#0f172a] transition-colors disabled:opacity-50 ${selected ? 'inline-flex' : 'hidden'}`}
                >
                  {isInstalling ? (
                    <svg className="w-[13px] h-[13px] animate-spin" viewBox="0 0 16 16" fill="none"><circle cx="8" cy="8" r="6" stroke="currentColor" strokeWidth="2" strokeDasharray="28" strokeDashoffset="8" /></svg>
                  ) : (
                    <svg className="w-[13px] h-[13px]" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="2"><path d="M2 3l2 10h8l2-10H2z" /><path d="M6 3V1h4v2" /><line x1="4" y1="6" x2="12" y2="6" /></svg>
                  )}
                  {isInstalling ? '下载中...' : '使用'}
                </button>

                {/* Top row */}
                <div className="flex items-start gap-3">
                  <div className="w-11 h-11 rounded-[10px] flex items-center justify-center text-[22px] font-semibold flex-shrink-0" style={{ backgroundColor: e.color + '15', color: e.color }}>
                    {e.icon}
                  </div>
                  <div className="flex-1 min-w-0">
                    <div className="font-semibold text-[15px] text-[#0f172a] tracking-[-0.1px]">{getExpertName(e)}</div>
                    <div className="text-xs text-[#94a3b8] mt-0.5">{e.profession}</div>
                    <div className="text-xs text-[#64748b] mt-1.5 leading-[1.4]">{e.desc}</div>
                  </div>
                </div>

                {/* Tags */}
                <div className="flex gap-1.5 flex-wrap">
                  {e.tags.map(t => (
                    <span key={t} className="text-[10px] font-medium px-2 py-0.5 rounded bg-[#f0fdf4] text-[#047857] tracking-[0.2px]">{t}</span>
                  ))}
                </div>

                {/* Config */}
                <div className="flex gap-4 text-[11px] text-[#94a3b8] pt-2.5 border-t border-[#f1f5f9]">
                  <span>回合上限 <span className="font-semibold text-[#64748b]">{e.config.max_turn}</span></span>
                  <span>Token 预算 <span className="font-semibold text-[#64748b]">{(e.config.max_tokens / 1000).toFixed(0)}K</span></span>
                  <span>超时 <span className="font-semibold text-[#64748b]">{e.config.timeout_seconds}s</span></span>
                </div>

                {/* Detail (system prompt) */}
                <div className={`overflow-hidden transition-all duration-300 ${selected ? 'max-h-[200px] pt-3' : 'max-h-0 pt-0'}`}>
                  {selected && e.system_prompt && (
                    <>
                      <div className="text-[10px] font-semibold text-[#94a3b8] uppercase tracking-[0.4px] mb-1.5">System Prompt 预览</div>
                      <div className="text-xs text-[#64748b] italic bg-[#fff] p-2.5 rounded-md border border-[#f1f5f9] leading-[1.6] whitespace-pre-wrap max-h-[120px] overflow-y-auto">{e.system_prompt}</div>
                    </>
                  )}
                </div>

                {/* Selected hint */}
                {selected && (
                  <div className="text-[11px] text-[#047857] font-medium flex items-center gap-1">
                    <svg className="w-3 h-3" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="2"><polyline points="5 8 8 11 11 5" /></svg>
                    已选中
                  </div>
                )}
              </div>
            )
          }) : (
            <div className="text-[13px] text-[#94a3b8] py-3">未找到匹配的专家</div>
          )}
        </div>
      )}

      {/* Team Tab */}
      {tab === 'team' && !loading && (
        <div className="grid gap-2.5" style={{ gridTemplateColumns: 'repeat(auto-fill, minmax(280px, 1fr))' }}>
          {filteredTeams.length > 0 ? filteredTeams.map(t => {
            const expanded = expandedTeamId === t.id
            const isTeamInstalling = installingId === t.id
            return (
              <div
                key={t.id}
                onClick={() => setExpandedTeamId(expandedTeamId === t.id ? null : t.id)}
                className={`bg-white border rounded-[10px] p-5 flex flex-col gap-3.5 relative transition-colors cursor-pointer ${
                  expanded
                    ? 'border-[#a7f3d0] border-l-[3px] border-l-[#a7f3d0] bg-[#f0fdf4]'
                    : 'border-[#e2e8f0] border-l-[3px] border-l-transparent hover:border-[#cbd5e1] hover:shadow-sm'
                }`}
              >
                {/* Use button */}
                <button
                  onClick={(ev) => { ev.stopPropagation(); handleUseTeam(t.id) }}
                  disabled={isTeamInstalling}
                  className={`absolute top-3 right-4 items-center gap-1 px-3.5 py-1.5 rounded-md text-xs font-semibold text-white bg-[#a7f3d0] hover:bg-[#059669] transition-colors disabled:opacity-50 ${expanded ? 'inline-flex' : 'hidden'}`}
                >
                  {isTeamInstalling ? (
                    <svg className="w-[13px] h-[13px] animate-spin" viewBox="0 0 16 16" fill="none"><circle cx="8" cy="8" r="6" stroke="currentColor" strokeWidth="2" strokeDasharray="28" strokeDashoffset="8" /></svg>
                  ) : (
                    <svg className="w-[13px] h-[13px]" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="2"><path d="M2 3l2 10h8l2-10H2z" /><path d="M6 3V1h4v2" /><line x1="4" y1="6" x2="12" y2="6" /></svg>
                  )}
                  {isTeamInstalling ? '下载中...' : '使用'}
                </button>

                {/* Header */}
                <div className="flex items-start gap-3">
                  <div className="w-11 h-11 rounded-[10px] flex items-center justify-center text-[22px] flex-shrink-0 bg-gradient-to-br from-[#a7f3d0] to-[#6ee7b7]">
                    {t.icon}
                  </div>
                  <div className="flex-1 min-w-0">
                    <div className="font-semibold text-[15px] text-[#0f172a] tracking-[-0.1px]">{getTeamName(t)}</div>
                    <div className="text-[11px] text-[#94a3b8] mt-0.5">领队：{getTeamLead(t)} · {getTeamLeadProf(t)}</div>
                    <div className="text-xs text-[#64748b] mt-1.5 leading-[1.4]">{t.desc}</div>
                  </div>
                </div>

                {/* Expandable detail */}
                <div className={`overflow-hidden transition-all duration-300 ${expanded ? 'max-h-[420px] overflow-y-auto pt-1' : 'max-h-0 pt-0'}`}>
                  {/* Members */}
                  <div className="flex flex-col gap-1.5">
                    <div className="text-[10px] font-semibold text-[#94a3b8] uppercase tracking-[0.4px]">团队成员 ({t.members.length}人)</div>
                    {t.members.map(m => (
                      <div key={m.id} className="flex items-center gap-2.5 py-1.5 px-2.5 rounded-md bg-[#f8fafc] text-[13px]">
                        <div className={`w-7 h-7 rounded-full flex items-center justify-center text-[13px] font-semibold text-white flex-shrink-0 ${m.role === 'lead' ? 'bg-[#10b981]' : 'bg-[#94a3b8]'}`}>{m.avatar ? (m.avatar.startsWith('avatars/') ? m.avatar.replace(/^avatars\//, '').charAt(0) : m.avatar.charAt(0)) : getMemberName(m).charAt(0)}</div>
                        <div className="flex-1 min-w-0">
                          <div className="font-medium text-[#0f172a] text-[13px]">{getMemberName(m)}</div>
                          <div className="text-[11px] text-[#94a3b8]">{getMemberProfession(m)}</div>
                        </div>
                        <span className={`text-[10px] font-semibold px-2 py-0.5 rounded-lg flex-shrink-0 ${m.role === 'lead' ? 'bg-[#ecfdf5] text-[#047857]' : 'bg-[#f1f5f9] text-[#64748b]'}`}>
                          {m.role === 'lead' ? '领队' : '成员'}
                        </span>
                      </div>
                    ))}
                  </div>

                  {/* Config list */}
                  <div className="flex flex-col gap-1.5 mt-3">
                    {t.skills.length > 0 && (
                      <div className="flex items-center gap-2 py-1.5 px-2.5 bg-white rounded-md border border-[#f1f5f9]">
                        <span className="text-[11px] text-[#94a3b8] flex-shrink-0">Skills</span>
                        <span className="text-xs font-medium text-[#0f172a]">{t.skills.join(', ')}</span>
                      </div>
                    )}
                    {t.mcp.length > 0 && (
                      <div className="flex items-center gap-2 py-1.5 px-2.5 bg-white rounded-md border border-[#f1f5f9]">
                        <span className="text-[11px] text-[#94a3b8] flex-shrink-0">MCP</span>
                        <span className="text-xs font-medium text-[#0f172a]">{t.mcp.join(', ')}</span>
                      </div>
                    )}
                    <div className="flex items-center gap-2 py-1.5 px-2.5 bg-white rounded-md border border-[#f1f5f9]">
                      <span className="text-[11px] text-[#94a3b8] flex-shrink-0">工作模式</span>
                      <span className="text-xs font-medium text-[#0f172a]">星型拓扑 — 主 Agent 调度，子 Agent 独立执行，结果汇总</span>
                    </div>
                  </div>
                </div>
              </div>
            )
          }) : (
            <div className="text-[13px] text-[#94a3b8] py-3">未找到匹配的团队</div>
          )}
        </div>
      )}
    </div>
  )
}
