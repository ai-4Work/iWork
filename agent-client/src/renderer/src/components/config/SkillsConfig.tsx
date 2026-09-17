import { useState, useEffect } from 'react'
import { useConfigStore } from '../../stores/configStore'
import { useCardTooltip } from './useCardTooltip'
import type { CreateCustomSkillRequest } from '../../types'

function Spinner() {
  return (
    <svg className="w-4 h-4 animate-spin text-[#94a3b8]" viewBox="0 0 16 16" fill="none">
      <circle cx="8" cy="8" r="6" stroke="currentColor" strokeWidth="2" strokeDasharray="28" strokeDashoffset="8" />
    </svg>
  )
}

export function SkillsConfig() {
  const [tab, setTab] = useState<'hub' | 'installed' | 'custom'>('hub')
  const [search, setSearch] = useState('')
  const [showCreate, setShowCreate] = useState(false)
  const [actionError, setActionError] = useState<string | null>(null)
  const { hoverProps, tooltip } = useCardTooltip()

  const {
    hubSkills, hubSkillsLoading, hubSkillsError,
    installedSkills, installedSkillsLoading, installedSkillsError,
    customSkills, customSkillsLoading, customSkillsError,
    installingSkillIds,
    loadAllSkills,
    installSkill, uninstallSkill, enableSkill, disableSkill,
    createCustomSkill, deleteCustomSkill
  } = useConfigStore()

  useEffect(() => {
    loadAllSkills()
  }, [])

  const installedIds = new Set(installedSkills.map(s => s.skill_id))

  const filtered = hubSkills.filter(s =>
    !search || s.skill_name.toLowerCase().includes(search.toLowerCase()) ||
    s.description.toLowerCase().includes(search.toLowerCase()) ||
    s.category.toLowerCase().includes(search.toLowerCase())
  )

  const handleInstall = async (skillId: string) => {
    setActionError(null)
    try {
      await installSkill(skillId)
    } catch (err: any) {
      setActionError(err.message || '安装失败')
    }
  }

  const handleUninstall = async (skillId: string) => {
    setActionError(null)
    try {
      await uninstallSkill(skillId)
    } catch (err: any) {
      setActionError(err.message || '卸载失败')
    }
  }

  const handleToggleEnabled = async (skillId: string, currentlyEnabled: boolean) => {
    setActionError(null)
    try {
      if (currentlyEnabled) {
        await disableSkill(skillId)
      } else {
        await enableSkill(skillId)
      }
    } catch (err: any) {
      setActionError(err.message || '操作失败')
    }
  }

  const handleDeleteCustom = async (skillId: string) => {
    setActionError(null)
    try {
      await deleteCustomSkill(skillId)
    } catch (err: any) {
      setActionError(err.message || '删除失败')
    }
  }

  return (
    <div>
      <div className="flex items-center gap-2 mb-5">
        <div className="flex gap-0.5 bg-[#f1f5f9] rounded-md p-0.5">
          {(['hub', 'installed', 'custom'] as const).map(t => (
            <button
              key={t}
              onClick={() => setTab(t)}
              className={`px-[18px] py-[7px] rounded-[5px] text-[13px] font-medium transition-colors ${
                tab === t ? 'bg-white text-[#0f172a] shadow-sm' : 'text-[#64748b] hover:text-[#0f172a] bg-transparent border-none cursor-pointer'
              }`}
            >
              {t === 'hub' ? 'Hub' : t === 'installed' ? '已安装' : '自己添加'}
            </button>
          ))}
        </div>
        {tab === 'hub' && (
          <div className="relative ml-auto">
            <svg className="w-4 h-4 absolute left-2.5 top-1/2 -translate-y-1/2 text-[#94a3b8]" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
              <circle cx="7" cy="7" r="4.5"/><line x1="10.5" y1="10.5" x2="14" y2="14"/>
            </svg>
            <input
              className="w-[200px] py-[6px] px-2.5 pl-[30px] border border-[#e2e8f0] rounded-md text-[13px] bg-white outline-none focus:border-[#a7f3d0]"
              placeholder="搜索 Skill..."
              value={search}
              onChange={(e) => setSearch(e.target.value)}
            />
          </div>
        )}
      </div>

      {actionError && (
        <div className="mb-3 px-3 py-2 bg-[#fef2f2] border border-[#fecaca] rounded-md text-[13px] text-[#dc2626] flex items-center gap-2">
          <span>{actionError}</span>
          <button onClick={() => setActionError(null)} className="ml-auto text-[#94a3b8] hover:text-[#0f172a] cursor-pointer">&times;</button>
        </div>
      )}

      {/* ── Hub Tab ── */}
      {tab === 'hub' && (
        <>
          {hubSkillsLoading && <div className="flex items-center gap-2 text-[#94a3b8] text-[13px] py-4"><Spinner /> 加载 Hub 列表...</div>}
          {hubSkillsError && <div className="text-[#dc2626] text-[13px] py-3">{hubSkillsError}</div>}
          {!hubSkillsLoading && !hubSkillsError && (
            <div className="grid grid-cols-4 gap-2.5">
              {filtered.map(s => {
                const installed = installedIds.has(s.skill_id)
                const installing = installingSkillIds.has(s.skill_id)
                return (
                  <div key={s.skill_id} {...hoverProps(s.skill_name, s.description)} className="bg-white border border-[#e2e8f0] rounded-[10px] p-[12px] flex gap-2.5 hover:border-[#cbd5e1] hover:shadow-sm transition-all">
                    <div className="w-[32px] h-[32px] rounded-md bg-[#f1f5f9] flex items-center justify-center flex-shrink-0 text-base">{s.icon}</div>
                    <div className="flex-1 min-w-0">
                      <div data-tip-name className="font-semibold text-[13px] text-[#0f172a] truncate">{s.skill_name}</div>
                      <div data-tip-desc className="text-xs text-[#94a3b8] mt-0.5 line-clamp-2">{s.description}</div>
                      <div className="flex gap-1.5 mt-1.5 flex-wrap">
                        <span className="inline-block text-[10px] font-medium px-1.5 py-0.5 rounded whitespace-nowrap bg-[#f0fdf4] text-[#047857]">{s.category}</span>
                        <span className="inline-block text-[10px] font-medium px-1.5 py-0.5 rounded whitespace-nowrap bg-[#f1f5f9] text-[#64748b]">v{s.version}</span>
                      </div>
                    </div>
                    <div className="flex-shrink-0 self-center">
                      {installing ? (
                        <span className="inline-flex items-center gap-1 px-3.5 py-1.5 rounded-md text-xs font-medium border border-[#e2e8f0] text-[#94a3b8] bg-[#f1f5f9]">
                          <Spinner /> 安装中
                        </span>
                      ) : (
                        <button
                          onClick={() => installed ? handleUninstall(s.skill_id) : handleInstall(s.skill_id)}
                          className={`px-3.5 py-1.5 rounded-md text-xs font-medium transition-colors border cursor-pointer ${
                            installed
                              ? 'border-[#e2e8f0] text-[#94a3b8] bg-[#f1f5f9]'
                              : 'border-[#a7f3d0] text-[#047857] bg-[#f0fdf4] hover:bg-[#a7f3d0]'
                          }`}
                        >
                          {installed ? '已安装' : '安装'}
                        </button>
                      )}
                    </div>
                  </div>
                )
              })}
              {filtered.length === 0 && <div className="text-[#94a3b8] text-[13px] py-3">未找到匹配的 Skill</div>}
            </div>
          )}
        </>
      )}

      {/* ── Installed Tab ── */}
      {tab === 'installed' && (
        <>
          {installedSkillsLoading && <div className="flex items-center gap-2 text-[#94a3b8] text-[13px] py-4"><Spinner /> 加载已安装列表...</div>}
          {installedSkillsError && <div className="text-[#dc2626] text-[13px] py-3">{installedSkillsError}</div>}
          {!installedSkillsLoading && !installedSkillsError && (
            <div>
              <div className="text-[11px] font-semibold text-[#94a3b8] uppercase tracking-wider mb-2">Hub 已安装 ({installedSkills?.length})</div>
              <div className="grid grid-cols-4 gap-2.5 mb-5">
                {installedSkills?.map(s => (
                  <div key={s.skill_id} {...hoverProps(s.skill_name, s.description)} className="bg-white border border-l-[3px] border-l-[#a7f3d0] border-[#e2e8f0] rounded-[10px] p-[12px] flex gap-2.5">
                    <div className="w-[32px] h-[32px] rounded-md bg-[#f1f5f9] flex items-center justify-center flex-shrink-0 text-base">{s.icon}</div>
                    <div className="flex-1 min-w-0">
                      <div data-tip-name className="font-semibold text-[13px] text-[#0f172a] truncate">{s.skill_name}</div>
                      <div data-tip-desc className="text-xs text-[#94a3b8] mt-0.5 line-clamp-2">{s.description}</div>
                      <span className="inline-block text-[10px] font-medium px-1.5 py-0.5 rounded whitespace-nowrap mt-1.5 bg-[#f0fdf4] text-[#047857]">{s.category}</span>
                    </div>
                    <div className="flex-shrink-0 self-center flex flex-col gap-1">
                      {/* <button
                        onClick={() => handleToggleEnabled(s.skill_id, s.enabled)}
                        className={`px-3.5 py-1.5 rounded-md text-xs font-medium transition-colors border cursor-pointer ${
                          s.enabled
                            ? 'border-[#e2e8f0] text-[#94a3b8] bg-[#f1f5f9]'
                            : 'border-[#fde68a] text-[#b45309] bg-[#fffbeb] hover:bg-[#fde68a]'
                        }`}
                      >
                        {s.enabled ? '禁用' : '启用'}
                      </button> */}
                      <button onClick={() => handleUninstall(s.skill_id)} className="px-3.5 py-1.5 rounded-md text-xs font-medium border border-[#e2e8f0] text-[#94a3b8] bg-[#f1f5f9] cursor-pointer">卸载</button>
                    </div>
                  </div>
                ))}
                {installedSkills?.length === 0 && <div className="text-[#94a3b8] text-[13px] py-3">暂无已安装的 Hub Skill</div>}
              </div>

              <div className="text-[11px] font-semibold text-[#94a3b8] uppercase tracking-wider mb-2">内置 Skills ({installedSkills.filter(s => s.source === 'builtin').length})</div>
              <div className="grid grid-cols-4 gap-2.5">
                {installedSkills.filter(s => s.source === 'builtin').map(s => (
                  <div key={s.skill_id} {...hoverProps(s.skill_name, s.description)} className="bg-white border border-l-[3px] border-l-[#a7f3d0] border-[#e2e8f0] rounded-[10px] p-[12px] flex gap-2.5">
                    <div className="w-[32px] h-[32px] rounded-md bg-[#f1f5f9] flex items-center justify-center flex-shrink-0 text-base">{s.icon}</div>
                    <div className="flex-1 min-w-0">
                      <div data-tip-name className="font-semibold text-[13px] text-[#0f172a] truncate">{s.skill_name}</div>
                      <div data-tip-desc className="text-xs text-[#94a3b8] mt-0.5 line-clamp-2">{s.description}</div>
                      <span className="inline-block text-[10px] font-medium px-1.5 py-0.5 rounded whitespace-nowrap mt-1.5 bg-[#f0fdf4] text-[#047857]">内置</span>
                    </div>
                    <div className="flex-shrink-0 self-center">
                      <button
                        onClick={() => handleToggleEnabled(s.skill_id, s.enabled)}
                        className={`px-3.5 py-1.5 rounded-md text-xs font-medium transition-colors border cursor-pointer ${
                          s.enabled
                            ? 'border-[#e2e8f0] text-[#94a3b8] bg-[#f1f5f9]'
                            : 'border-[#fde68a] text-[#b45309] bg-[#fffbeb] hover:bg-[#fde68a]'
                        }`}
                      >
                        {s.enabled ? '禁用' : '启用'}
                      </button>
                    </div>
                  </div>
                ))}
                {installedSkills.filter(s => s.source === 'builtin').length === 0 && <div className="text-[#94a3b8] text-[13px] py-3">暂无内置 Skill</div>}
              </div>
            </div>
          )}
        </>
      )}

      {/* ── Custom Tab ── */}
      {tab === 'custom' && (
        <>
          <div className="flex gap-2 mb-5">
            <button
              onClick={() => setShowCreate(true)}
              className="flex items-center gap-1.5 px-4 py-2 border border-dashed border-[#e2e8f0] rounded-md text-[13px] text-[#64748b] hover:border-[#a7f3d0] hover:text-[#047857] hover:bg-[#f0fdf4] transition-colors cursor-pointer bg-white"
            >
              <svg className="w-4 h-4" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round"><path d="M8 3v10M3 8h10"/></svg>
              创建自定义 Skill
            </button>
          </div>

          {customSkillsLoading && <div className="flex items-center gap-2 text-[#94a3b8] text-[13px] py-4"><Spinner /> 加载中...</div>}
          {customSkillsError && <div className="text-[#dc2626] text-[13px] py-3">{customSkillsError}</div>}
          {!customSkillsLoading && !customSkillsError && (
            <div className="grid grid-cols-4 gap-2.5">
              {customSkills.map(s => (
                <div key={s.skill_id} {...hoverProps(s.skill_name, s.description)} className="bg-white border border-l-[3px] border-l-[#a7f3d0] border-[#e2e8f0] rounded-[10px] p-[12px] flex gap-2.5">
                  <div className="w-[32px] h-[32px] rounded-md bg-[#f1f5f9] flex items-center justify-center flex-shrink-0 text-base">{s.icon}</div>
                  <div className="flex-1 min-w-0">
                    <div data-tip-name className="font-semibold text-[13px] text-[#0f172a] truncate">{s.skill_name}</div>
                    <div data-tip-desc className="text-xs text-[#94a3b8] mt-0.5 line-clamp-2">{s.description}</div>
                    <div className="flex gap-1.5 mt-1.5">
                      <span className="inline-block text-[10px] font-medium px-1.5 py-0.5 rounded whitespace-nowrap bg-[#f0fdf4] text-[#047857]">{s.category}</span>
                      <span className="inline-block text-[10px] font-medium px-1.5 py-0.5 rounded whitespace-nowrap bg-[#f1f5f9] text-[#64748b]">{new Date(s.created_at).toLocaleDateString()}</span>
                    </div>
                  </div>
                  <div className="flex-shrink-0 self-center">
                    <button onClick={() => handleDeleteCustom(s.skill_id)} className="px-3.5 py-1.5 rounded-md text-xs font-medium border border-[#e2e8f0] text-[#94a3b8] bg-[#f1f5f9] cursor-pointer">删除</button>
                  </div>
                </div>
              ))}
              {customSkills.length === 0 && <div className="text-[#94a3b8] text-[13px] py-3">暂无自定义 Skill，点击上方按钮创建</div>}
            </div>
          )}

          {showCreate && (
            <CreateSkillModal
              onClose={() => setShowCreate(false)}
              onSubmit={async (req) => {
                setActionError(null)
                try {
                  await createCustomSkill(req)
                  setShowCreate(false)
                } catch (err: any) {
                  setActionError(err.message || '创建失败')
                }
              }}
            />
          )}
        </>
      )}

      {tooltip}
    </div>
  )
}

function CreateSkillModal({ onClose, onSubmit }: {
  onClose: () => void
  onSubmit: (req: CreateCustomSkillRequest) => Promise<void>
}) {
  const [name, setName] = useState('')
  const [desc, setDesc] = useState('')
  const [prompt, setPrompt] = useState('')
  const [submitting, setSubmitting] = useState(false)

  const handleSubmit = async () => {
    if (!name.trim() || !prompt.trim()) return
    setSubmitting(true)
    try {
      await onSubmit({
        skill_name: name.trim(),
        description: desc.trim() || undefined,
        prompt: prompt.trim()
      })
    } finally {
      setSubmitting(false)
    }
  }

  const isValid = name.trim() && prompt.trim().length >= 10

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/20" onClick={onClose}>
      <div className="bg-white rounded-xl shadow-lg p-6 w-[480px] max-h-[80vh] overflow-y-auto" onClick={e => e.stopPropagation()}>
        <div className="flex items-center justify-between mb-4">
          <h3 className="text-[15px] font-semibold text-[#0f172a]">创建自定义 Skill</h3>
          <button onClick={onClose} className="text-[#94a3b8] hover:text-[#0f172a] cursor-pointer">
            <svg className="w-4 h-4" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="2"><path d="M4 4l8 8M12 4l-8 8"/></svg>
          </button>
        </div>

        <div className="space-y-3.5">
          <div>
            <label className="block text-[12px] font-medium text-[#64748b] mb-1">名称 *</label>
            <input className="w-full px-2.5 py-[7px] border border-[#e2e8f0] rounded-md text-[13px] outline-none focus:border-[#a7f3d0]" placeholder="例如：我的代码审查 Skill" value={name} onChange={e => setName(e.target.value)} />
          </div>
          <div>
            <label className="block text-[12px] font-medium text-[#64748b] mb-1">描述</label>
            <input className="w-full px-2.5 py-[7px] border border-[#e2e8f0] rounded-md text-[13px] outline-none focus:border-[#a7f3d0]" placeholder="这个 Skill 的用途" value={desc} onChange={e => setDesc(e.target.value)} />
          </div>
          <div>
            <label className="block text-[12px] font-medium text-[#64748b] mb-1">Prompt * <span className="text-[#94a3b8] font-normal">（最少 10 字符）</span></label>
            <textarea
              className="w-full px-2.5 py-[7px] border border-[#e2e8f0] rounded-md text-[13px] outline-none focus:border-[#a7f3d0] min-h-[120px] resize-y"
              placeholder="编写 Skill 的指令 prompt..."
              value={prompt}
              onChange={e => setPrompt(e.target.value)}
            />
            <div className="text-[11px] text-[#94a3b8] mt-1">{prompt.length} / 10 字符</div>
          </div>
        </div>

        <div className="flex justify-end gap-2 mt-5">
          <button onClick={onClose} className="px-4 py-1.5 rounded-md text-[13px] border border-[#e2e8f0] text-[#64748b] cursor-pointer">取消</button>
          <button onClick={handleSubmit} disabled={!isValid || submitting} className={`px-4 py-1.5 rounded-md text-[13px] font-medium cursor-pointer border ${
            isValid && !submitting
              ? 'border-[#a7f3d0] text-[#047857] bg-[#f0fdf4] hover:bg-[#a7f3d0]'
              : 'border-[#e2e8f0] text-[#cbd5e1] bg-[#f8fafc] cursor-not-allowed'
          }`}>
            {submitting ? '创建中...' : '创建'}
          </button>
        </div>
      </div>
    </div>
  )
}
