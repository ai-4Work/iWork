import { useState, useEffect } from 'react'
import { useConfigStore } from '../../stores/configStore'
import type { MemoryItem, RuleItem } from '../../types'

const TYPE_LABELS: Record<string, string> = {
  user: '用户偏好',
  feedback: '行为准则',
  project: '项目上下文',
  reference: '外部资源'
}

const TYPE_COLORS: Record<string, string> = {
  user: 'bg-blue-50 text-blue-600',
  feedback: 'bg-amber-50 text-amber-600',
  project: 'bg-emerald-50 text-emerald-600',
  reference: 'bg-purple-50 text-purple-600'
}

export function MemoryConfig() {
  const {
    memories, rules,
    loadMemories, saveMemoryAction, deleteMemoryAction,
    loadRules, saveRuleAction, deleteRuleAction
  } = useConfigStore()

  const [showModal, setShowModal] = useState(false)
  const [modalType, setModalType] = useState<'memory' | 'rule'>('memory')
  const [editingItem, setEditingItem] = useState<MemoryItem | RuleItem | null>(null)
  const [submitting, setSubmitting] = useState(false)

  // Form state
  const [formName, setFormName] = useState('')
  const [formDesc, setFormDesc] = useState('')
  const [formContent, setFormContent] = useState('')
  const [formType, setFormType] = useState<string>('user')
  const [formProtected, setFormProtected] = useState(false)
  const [formPriority, setFormPriority] = useState(0)

  useEffect(() => {
    loadMemories()
    loadRules()
  }, [])

  // ── Memory actions ──

  const openNewMemory = () => {
    setModalType('memory')
    setEditingItem(null)
    setFormName('')
    setFormDesc('')
    setFormContent('')
    setFormType('user')
    setFormProtected(false)
    setShowModal(true)
  }

  const openEditMemory = (item: MemoryItem) => {
    setModalType('memory')
    setEditingItem(item)
    setFormName(item.name)
    setFormDesc(item.description)
    setFormContent(item.content)
    setFormType(item.type)
    setFormProtected(item.protected)
    setShowModal(true)
  }

  const handleDeleteMemory = async (item: MemoryItem) => {
    if (!confirm(`确定删除记忆 "${item.name}"？`)) return
    try {
      await deleteMemoryAction(item.id)
    } catch (err: any) {
      alert(err.message || '删除失败')
    }
  }

  // ── Rule actions ──

  const openNewRule = () => {
    setModalType('rule')
    setEditingItem(null)
    setFormName('')
    setFormDesc('')
    setFormContent('')
    setFormPriority(0)
    setShowModal(true)
  }

  const openEditRule = (item: RuleItem) => {
    setModalType('rule')
    setEditingItem(item)
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

  // ── Submit ──

  const handleSubmit = async () => {
    if (!formName.trim() || !formContent.trim()) return
    setSubmitting(true)
    try {
      if (modalType === 'memory') {
        await saveMemoryAction({
          name: formName.trim(),
          description: formDesc.trim(),
          type: formType,
          content: formContent.trim(),
          protected: formProtected
        })
      } else {
        await saveRuleAction({
          name: formName.trim(),
          description: formDesc.trim(),
          content: formContent.trim(),
          priority: formPriority
        })
      }
      setShowModal(false)
    } catch (err: any) {
      alert(err.message || '保存失败')
    } finally {
      setSubmitting(false)
    }
  }

  const isEditing = editingItem !== null
  const isValid = formName.trim().length > 0 && formContent.trim().length >= 10

  return (
    <div>
      {/* ═══════════ 聊天记忆 ═══════════ */}
      <div className="flex items-center justify-between mb-2">
        <div className="text-[11px] font-semibold text-[#94a3b8] uppercase tracking-wider">聊天记忆</div>
        <button
          onClick={openNewMemory}
          className="flex items-center gap-1 px-2 py-0.5 rounded text-[11px] font-medium text-[#047857] bg-[#f0fdf4] border border-[#a7f3d0] hover:bg-[#a7f3d0] transition-colors cursor-pointer"
        >
          <svg className="w-3 h-3" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round"><path d="M8 3v10M3 8h10"/></svg>
          新建
        </button>
      </div>
      <div className="flex flex-col gap-1.5 mb-6">
        {memories.length === 0 ? (
          <div className="text-[12px] text-[#94a3b8] py-3 text-center">暂无记忆</div>
        ) : (
          memories.map(m => (
            <div key={m.id} className="bg-white border border-[#e2e8f0] rounded-md py-2.5 px-3.5 flex items-center gap-3 hover:border-[#cbd5e1] transition-colors group">
              <div className="flex-1 min-w-0">
                <div className="text-[13px] text-[#0f172a] font-medium truncate">{m.name}</div>
                <div className="flex items-center gap-2 mt-0.5">
                  <span className={`text-[10px] px-1.5 py-px rounded-full font-medium ${TYPE_COLORS[m.type] || 'bg-gray-50 text-gray-500'}`}>
                    {TYPE_LABELS[m.type] || m.type}
                  </span>
                  {m.description && <span className="text-[11px] text-[#94a3b8] truncate">{m.description}</span>}
                </div>
              </div>
              {m.protected && (
                <span className="text-[11px] text-[#94a3b8] flex-shrink-0" title="受保护 — AI 不可修改或删除">🔒</span>
              )}
              <button onClick={() => openEditMemory(m)} className="text-[#94a3b8] hover:text-[#047857] hover:bg-[#f0fdf4] p-1 rounded transition-colors bg-transparent border-none cursor-pointer flex-shrink-0 opacity-0 group-hover:opacity-100">
                <svg width="12" height="12" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="2"><path d="M2 14h2l8-8-2-2-8 8v2z"/><path d="M12 3l2 2"/></svg>
              </button>
              <button onClick={() => handleDeleteMemory(m)} className="text-[#94a3b8] hover:text-[#ef4444] hover:bg-[#fef2f2] p-1 rounded transition-colors bg-transparent border-none cursor-pointer flex-shrink-0 opacity-0 group-hover:opacity-100">
                <svg width="12" height="12" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><polyline points="2 4 14 4 12 16 4 16 2 4"/><line x1="6" y1="7" x2="6" y2="12"/><line x1="10" y1="7" x2="10" y2="12"/></svg>
              </button>
            </div>
          ))
        )}
      </div>

      {/* ═══════════ Rules ═══════════ */}
      <div className="flex items-center justify-between mb-2 mt-5">
        <div className="text-[11px] font-semibold text-[#94a3b8] uppercase tracking-wider">Rules</div>
        <button
          onClick={openNewRule}
          className="flex items-center gap-1 px-2 py-0.5 rounded text-[11px] font-medium text-[#047857] bg-[#f0fdf4] border border-[#a7f3d0] hover:bg-[#a7f3d0] transition-colors cursor-pointer"
        >
          <svg className="w-3 h-3" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round"><path d="M8 3v10M3 8h10"/></svg>
          新建
        </button>
      </div>
      <div className="flex flex-col gap-1.5">
        {rules.length === 0 ? (
          <div className="text-[12px] text-[#94a3b8] py-3 text-center">暂无规则</div>
        ) : (
          rules.map(r => (
            <div key={r.id} className="bg-white border border-[#e2e8f0] rounded-md py-2.5 px-3.5 flex items-center gap-3 hover:border-[#cbd5e1] transition-colors group">
              <div className="flex-1 min-w-0">
                <div className="text-[13px] text-[#0f172a] font-medium truncate">{r.name}</div>
                <div className="flex items-center gap-2 mt-0.5">
                  <span className="text-[10px] px-1.5 py-px rounded-full font-medium bg-indigo-50 text-indigo-600">
                    优先级 {r.priority}
                  </span>
                  {r.description && <span className="text-[11px] text-[#94a3b8] truncate">{r.description}</span>}
                </div>
              </div>
              <button onClick={() => openEditRule(r)} className="text-[#94a3b8] hover:text-[#047857] hover:bg-[#f0fdf4] p-1 rounded transition-colors bg-transparent border-none cursor-pointer flex-shrink-0 opacity-0 group-hover:opacity-100">
                <svg width="12" height="12" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="2"><path d="M2 14h2l8-8-2-2-8 8v2z"/><path d="M12 3l2 2"/></svg>
              </button>
              <button onClick={() => handleDeleteRule(r)} className="text-[#94a3b8] hover:text-[#ef4444] hover:bg-[#fef2f2] p-1 rounded transition-colors bg-transparent border-none cursor-pointer flex-shrink-0 opacity-0 group-hover:opacity-100">
                <svg width="12" height="12" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><polyline points="2 4 14 4 12 16 4 16 2 4"/><line x1="6" y1="7" x2="6" y2="12"/><line x1="10" y1="7" x2="10" y2="12"/></svg>
              </button>
            </div>
          ))
        )}
      </div>

      {/* ═══════════ Modal ═══════════ */}
      {showModal && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/20" onClick={() => setShowModal(false)}>
          <div className="bg-white rounded-xl shadow-lg p-6 w-[520px] max-h-[85vh] overflow-y-auto" onClick={e => e.stopPropagation()}>
            <div className="flex items-center justify-between mb-4">
              <h3 className="text-[15px] font-semibold text-[#0f172a]">
                {isEditing ? (modalType === 'memory' ? '编辑记忆' : '编辑规则') : (modalType === 'memory' ? '新建记忆' : '新建规则')}
              </h3>
              <button onClick={() => setShowModal(false)} className="text-[#94a3b8] hover:text-[#0f172a] cursor-pointer">
                <svg className="w-4 h-4" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="2"><path d="M4 4l8 8M12 4l-8 8"/></svg>
              </button>
            </div>

            <div className="space-y-3.5">
              {/* Name */}
              <div>
                <label className="block text-[12px] font-medium text-[#64748b] mb-1">
                  文件名 * <span className="text-[#94a3b8] font-normal">（如 user_role.md）</span>
                </label>
                <input
                  className="w-full px-2.5 py-[7px] border border-[#e2e8f0] rounded-md text-[13px] outline-none focus:border-[#a7f3d0]"
                  placeholder={modalType === 'memory' ? 'feedback_testing.md' : 'always-typescript.md'}
                  value={formName}
                  onChange={e => setFormName(e.target.value)}
                />
              </div>

              {/* Description */}
              <div>
                <label className="block text-[12px] font-medium text-[#64748b] mb-1">描述</label>
                <input
                  className="w-full px-2.5 py-[7px] border border-[#e2e8f0] rounded-md text-[13px] outline-none focus:border-[#a7f3d0]"
                  placeholder="一行描述，用于索引展示"
                  value={formDesc}
                  onChange={e => setFormDesc(e.target.value)}
                />
              </div>

              {/* Memory: type + protected */}
              {modalType === 'memory' && (
                <div className="flex gap-3">
                  <div className="flex-1">
                    <label className="block text-[12px] font-medium text-[#64748b] mb-1">类型 *</label>
                    <select
                      className="w-full px-2.5 py-[7px] border border-[#e2e8f0] rounded-md text-[13px] outline-none focus:border-[#a7f3d0] bg-white"
                      value={formType}
                      onChange={e => setFormType(e.target.value)}
                    >
                      <option value="user">user — 用户角色/偏好</option>
                      <option value="feedback">feedback — 行为准则/反馈</option>
                      <option value="project">project — 项目上下文</option>
                      <option value="reference">reference — 外部资源</option>
                    </select>
                  </div>
                  <div className="flex items-end pb-[7px]">
                    <label className="flex items-center gap-1.5 cursor-pointer">
                      <input
                        type="checkbox"
                        checked={formProtected}
                        onChange={e => setFormProtected(e.target.checked)}
                        className="w-3.5 h-3.5 rounded border-[#e2e8f0] accent-[#047857]"
                      />
                      <span className="text-[12px] text-[#64748b]">受保护（AI 不可修改）</span>
                    </label>
                  </div>
                </div>
              )}

              {/* Rule: priority */}
              {modalType === 'rule' && (
                <div>
                  <label className="block text-[12px] font-medium text-[#64748b] mb-1">优先级 * <span className="text-[#94a3b8] font-normal">（越高越靠前，默认 0）</span></label>
                  <input
                    type="number"
                    className="w-full px-2.5 py-[7px] border border-[#e2e8f0] rounded-md text-[13px] outline-none focus:border-[#a7f3d0]"
                    value={formPriority}
                    onChange={e => setFormPriority(Number(e.target.value))}
                  />
                </div>
              )}

              {/* Content */}
              <div>
                <label className="block text-[12px] font-medium text-[#64748b] mb-1">
                  内容 * <span className="text-[#94a3b8] font-normal">（Markdown，最少 10 字符）</span>
                </label>
                <textarea
                  className="w-full px-2.5 py-[7px] border border-[#e2e8f0] rounded-md text-[13px] outline-none focus:border-[#a7f3d0] min-h-[140px] resize-y font-mono"
                  placeholder={modalType === 'memory'
                    ? '# User Role\n\nSenior backend developer...'
                    : '# Rule\n\n始终使用 TypeScript...'}
                  value={formContent}
                  onChange={e => setFormContent(e.target.value)}
                />
                <div className="text-[11px] text-[#94a3b8] mt-1">{formContent.length} 字符</div>
              </div>
            </div>

            <div className="flex justify-end gap-2 mt-5">
              <button onClick={() => setShowModal(false)} className="px-4 py-1.5 rounded-md text-[13px] border border-[#e2e8f0] text-[#64748b] cursor-pointer">取消</button>
              <button
                onClick={handleSubmit}
                disabled={!isValid || submitting}
                className={`px-4 py-1.5 rounded-md text-[13px] font-medium cursor-pointer border ${
                  isValid && !submitting
                    ? 'border-[#a7f3d0] text-[#047857] bg-[#f0fdf4] hover:bg-[#a7f3d0]'
                    : 'border-[#e2e8f0] text-[#cbd5e1] bg-[#f8fafc] cursor-not-allowed'
                }`}
              >
                {submitting ? '保存中...' : isEditing ? '保存' : '创建'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
