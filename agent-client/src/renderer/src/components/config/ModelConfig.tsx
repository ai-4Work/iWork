/** 「模型配置」页（docs/chapters/19-权限管理RBAC.md 的模型配置一节）。
 *
 *  管理员在这里维护 LLM 清单；配好的模型出现在聊天下拉里，用户选了之后**真正用选中的
 *  模型发请求**（改造前 `model` 只落库不进请求，选什么都发同一个模型）。
 *
 *  闸门一览（缺了各自怎样）：
 *  | 闸门 | 点 | 缺了 |
 *  | 列表本体 | `system:model:list` | 整页一句提示，一个请求不发 |
 *  | 新增 | `system:model:add` | 不渲染按钮 |
 *  | 编辑 / 启停 | `system:model:edit` | 不出这些控件 |
 *  | 删除 | `system:model:remove` | 不出删除 |
 *  | 连接测试 | `system:model:test` | 不出测试按钮 |
 *
 *  「连接测试」单列一个权限点：它是唯一会拿库里的密钥真发一次请求的接口，
 *  与只读的 `list` 不是同一个信任级。
 */
import { useEffect, useRef, useState } from 'react'
import {
  createModel, deleteModel, fetchModelList, testModel, updateModel,
  type LlmModelAdmin, type LlmModelPayload, type ModelTestResult
} from '../../services/api'
import { usePermi } from '../../stores/authStore'
import { showToast } from '../../utils/toast'
import { ModelForm } from './ModelForm'

/** 部署类型翻译。表里只出两个字，详情在悬停提示里。 */
const DEPLOY_LABEL: Record<string, string> = {
  public: '公网',
  intranet: '内网',
}

export function ModelConfig() {
  // 一列/一个动作一个点。全都无条件调用，次序别动。
  const canList = usePermi('system:model:list')
  const canAdd = usePermi('system:model:add')
  const canEdit = usePermi('system:model:edit')
  const canRemove = usePermi('system:model:remove')
  const canTest = usePermi('system:model:test')

  const [models, setModels] = useState<LlmModelAdmin[]>([])
  const [search, setSearch] = useState('')
  const [loading, setLoading] = useState(true)
  /** 正在提交启停的那一行 —— 请求在飞时禁掉它的按钮，免得连点打架 */
  const [busyKey, setBusyKey] = useState<string | null>(null)
  /** 正在跑连接测试的那一行 */
  const [testingKey, setTestingKey] = useState<string | null>(null)
  /** 最近一次连接测试的结果，挂在表下方。失败要显示端点原文，弹 toast 会一闪而过 */
  const [testResult, setTestResult] = useState<{ key: string; result: ModelTestResult } | null>(null)

  const [formOpen, setFormOpen] = useState(false)
  /** 编辑的是哪一行；`null` + `formOpen` = 新增 */
  const [editing, setEditing] = useState<LlmModelAdmin | null>(null)

  const fetchedRef = useRef(false)

  useEffect(() => {
    if (!canList || fetchedRef.current) return
    fetchedRef.current = true
    void load()
  }, [canList])

  const load = async () => {
    setLoading(true)
    try {
      const res = await fetchModelList()
      setModels(res.models)
    } catch (err) {
      showToast(err instanceof Error ? err.message : '模型列表加载失败')
    } finally {
      setLoading(false)
    }
  }

  /** 写完之后只重拉列表。**不清前端本地的任何模型缓存** —— 聊天那侧的下拉是另一个
   *  store，它下次打开时会自己按 TTL 重拉；这里强行同步只会让两处状态各说各话。 */
  const reload = async () => {
    try {
      const res = await fetchModelList()
      setModels(res.models)
    } catch {
      /* 重拉失败就保持现状，下次操作会再对齐一次 */
    }
  }

  const handleSubmit = async (modelKey: string, payload: LlmModelPayload): Promise<boolean> => {
    try {
      if (editing) {
        await updateModel(editing.model_key, payload)
        showToast('已保存')
      } else {
        await createModel(modelKey, payload)
        showToast('已新增')
      }
      await reload()
      return true
    } catch (err) {
      showToast(err instanceof Error ? err.message : '保存失败')
      return false
    }
  }

  const handleToggle = async (m: LlmModelAdmin) => {
    const next = !m.enabled
    if (!next && !confirm(`停用「${m.display_name}」？存量会话会回落到默认模型。`)) return
    setBusyKey(m.model_key)
    try {
      await updateModel(m.model_key, { enabled: next })
      showToast(next ? '已启用' : '已停用')
      await reload()
    } catch (err) {
      showToast(err instanceof Error ? err.message : '状态修改失败')
    } finally {
      setBusyKey(null)
    }
  }

  const handleDelete = async (m: LlmModelAdmin) => {
    if (!confirm(`删除「${m.display_name}」？此操作不可撤销。`)) return
    try {
      await deleteModel(m.model_key)
      showToast('已删除')
      if (testResult?.key === m.model_key) setTestResult(null)
      await reload()
    } catch (err) {
      showToast(err instanceof Error ? err.message : '删除失败')
    }
  }

  const handleTest = async (m: LlmModelAdmin) => {
    setTestingKey(m.model_key)
    setTestResult(null)
    try {
      const result = await testModel(m.model_key)
      setTestResult({ key: m.model_key, result })
    } catch (err) {
      // 404（模型不存在）/ 403 走这里；端点自身的报错是 200 + ok=false，落在上面那条
      showToast(err instanceof Error ? err.message : '连接测试失败')
    } finally {
      setTestingKey(null)
    }
  }

  if (!canList) {
    return (
      <div className="text-[13px] text-[#64748b]">
        你没有 `system:model:list` 权限，看不到模型清单。
      </div>
    )
  }
  if (loading) {
    return <div className="text-[13px] text-[#64748b]">加载中…</div>
  }

  const q = search.trim().toLowerCase()
  const visible = q
    ? models.filter(
        (m) =>
          m.display_name.toLowerCase().includes(q) ||
          m.model_key.toLowerCase().includes(q) ||
          m.model_api_name.toLowerCase().includes(q)
      )
    : models

  const readOnly = !canAdd && !canEdit && !canRemove && !canTest

  return (
    <div className="flex flex-col gap-4">


      <div className="flex items-center gap-3">
        {canAdd && (
          <button
            onClick={() => { setEditing(null); setFormOpen(true) }}
            className="px-3 py-1.5 rounded-md border border-[#a7f3d0] bg-[#f0fdf4] text-[12px] text-[#047857] cursor-pointer hover:bg-[#a7f3d0]"
          >
            新增模型
          </button>
        )}
        <input
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          spellCheck={false}
          placeholder="搜索显示名、标识或模型名"
          className="w-[280px] px-2.5 py-1.5 border border-[#e2e8f0] rounded-md text-[13px] outline-none focus:border-[#a7f3d0] placeholder:text-[#cbd5e1]"
        />
        <span className="text-[12px] text-[#94a3b8]">
          {visible.length} / {models.length} 条
        </span>
      </div>

      <div className="border border-[#e2e8f0] rounded-lg bg-white overflow-x-auto">
        <table className="border-collapse w-full">
          <thead>
            <tr className="bg-[#f8fafc]">
              <th className="text-left text-[12px] font-semibold text-[#334155] px-4 py-2 border-b border-[#e2e8f0]">显示名</th>
              <th className="text-left text-[12px] font-semibold text-[#334155] px-4 py-2 border-b border-[#e2e8f0] w-[80px]">部署</th>
              <th className="text-left text-[12px] font-semibold text-[#334155] px-4 py-2 border-b border-[#e2e8f0] w-[120px]">协议</th>
              <th className="text-left text-[12px] font-semibold text-[#334155] px-4 py-2 border-b border-[#e2e8f0]">模型名 / 端点</th>
              <th className="text-left text-[12px] font-semibold text-[#334155] px-4 py-2 border-b border-[#e2e8f0] w-[90px]">上下文</th>
              <th className="text-left text-[12px] font-semibold text-[#334155] px-4 py-2 border-b border-[#e2e8f0] w-[100px]">压缩阈值</th>
              <th className="text-left text-[12px] font-semibold text-[#334155] px-4 py-2 border-b border-[#e2e8f0] w-[90px]">输出</th>
              <th className="text-left text-[12px] font-semibold text-[#334155] px-4 py-2 border-b border-[#e2e8f0] w-[80px]">超时</th>
              <th className="text-left text-[12px] font-semibold text-[#334155] px-4 py-2 border-b border-[#e2e8f0] w-[110px]">能力</th>
              <th className="text-left text-[12px] font-semibold text-[#334155] px-4 py-2 border-b border-[#e2e8f0] w-[110px]">状态</th>
              <th className="text-left text-[12px] font-semibold text-[#334155] px-4 py-2 border-b border-[#e2e8f0] w-[210px]">操作</th>
            </tr>
          </thead>
          <tbody>
            {visible.map((m) => {
              // 公网模型没密钥 = 每次请求都 401。这里主动标出来，别等人去试。
              const missingKey = m.deployment_type === 'public'
                && m.protocol === 'openai_compatible' && !m.has_api_key
              return (
                <tr key={m.model_key} className="hover:bg-[#f8fafc]">
                  <td className="px-4 py-2 border-b border-[#f1f5f9] text-[13px] text-[#334155]">
                    <div className="truncate">{m.display_name}</div>
                    <div className="text-[11px] text-[#94a3b8] font-mono">{m.model_key}</div>
                  </td>
                  <td className="px-4 py-2 border-b border-[#f1f5f9]">
                    <span className={`text-[11px] px-2 py-0.5 rounded-full ${
                      m.deployment_type === 'public'
                        ? 'bg-[rgba(191,219,254,0.6)] text-[#1d4ed8]'
                        : 'bg-[#f1f5f9] text-[#64748b]'
                    }`}>
                      {DEPLOY_LABEL[m.deployment_type] ?? m.deployment_type}
                    </span>
                  </td>
                  <td className="px-4 py-2 border-b border-[#f1f5f9] text-[12px] text-[#64748b]">
                    {m.protocol === 'anthropic' ? 'Anthropic' : 'OpenAI 兼容'}
                  </td>
                  <td className="px-4 py-2 border-b border-[#f1f5f9]">
                    <div className="text-[12px] text-[#334155] font-mono truncate">{m.model_api_name}</div>
                    <div className="text-[11px] text-[#94a3b8] font-mono truncate" title={m.base_url}>
                      {m.base_url || '（官方端点）'}
                    </div>
                  </td>
                  <td className="px-4 py-2 border-b border-[#f1f5f9] text-[12px] text-[#64748b]">
                    {m.context_window.toLocaleString()}
                  </td>
                  <td className="px-4 py-2 border-b border-[#f1f5f9] text-[12px] text-[#64748b]">
                    {m.compress_threshold_tokens !== null ? (
                      m.compress_threshold_tokens.toLocaleString()
                    ) : (
                      <span
                        className="text-[#94a3b8]"
                        title="未设绝对阈值，按上下文窗口折算（build 80% / 问答 55%）"
                      >
                        自动
                      </span>
                    )}
                  </td>
                  <td className="px-4 py-2 border-b border-[#f1f5f9] text-[12px] text-[#64748b]">
                    {m.max_output_tokens.toLocaleString()}
                  </td>
                  <td className="px-4 py-2 border-b border-[#f1f5f9] text-[12px] text-[#64748b]">
                    {m.timeout_seconds}s
                  </td>
                  <td className="px-4 py-2 border-b border-[#f1f5f9]">
                    <span className="flex flex-wrap items-center gap-1">
                      <span
                        title="支持工具调用（supports_tools）"
                        className={`text-[11px] px-2 py-0.5 rounded-full ${
                          m.supports_tools
                            ? 'bg-[rgba(167,243,208,0.5)] text-[#047857]'
                            : 'bg-[#f1f5f9] text-[#cbd5e1]'
                        }`}
                      >
                        工具
                      </span>
                      <span
                        title="支持思考（supports_thinking）"
                        className={`text-[11px] px-2 py-0.5 rounded-full ${
                          m.supports_thinking
                            ? 'bg-[rgba(167,243,208,0.5)] text-[#047857]'
                            : 'bg-[#f1f5f9] text-[#cbd5e1]'
                        }`}
                      >
                        思考
                      </span>
                    </span>
                  </td>
                  <td className="px-4 py-2 border-b border-[#f1f5f9]">
                    <span className={`text-[11px] px-2 py-0.5 rounded-full ${
                      m.enabled
                        ? 'bg-[rgba(167,243,208,0.5)] text-[#047857]'
                        : 'bg-[#f1f5f9] text-[#94a3b8]'
                    }`}>
                      {m.enabled ? '启用' : '已停用'}
                    </span>
                    {missingKey && (
                      <div className="text-[11px] text-[#b45309] mt-0.5">缺密钥</div>
                    )}
                  </td>
                  <td className="px-4 py-2 border-b border-[#f1f5f9]">
                    <span className="flex flex-wrap items-center gap-2">
                      {canTest && (
                        <button
                          onClick={() => void handleTest(m)}
                          disabled={testingKey === m.model_key}
                          className="text-[12px] text-[#047857] bg-transparent border-none cursor-pointer p-0 hover:underline disabled:text-[#cbd5e1] disabled:cursor-not-allowed"
                        >
                          {testingKey === m.model_key ? '测试中…' : '连接测试'}
                        </button>
                      )}
                      {canEdit && (
                        <>
                          <button
                            onClick={() => { setEditing(m); setFormOpen(true) }}
                            className="text-[12px] text-[#047857] bg-transparent border-none cursor-pointer p-0 hover:underline"
                          >
                            编辑
                          </button>
                          <button
                            onClick={() => void handleToggle(m)}
                            disabled={busyKey === m.model_key}
                            className={`text-[12px] bg-transparent border-none cursor-pointer p-0 hover:underline disabled:text-[#cbd5e1] ${
                              m.enabled ? 'text-[#b45309]' : 'text-[#047857]'
                            }`}
                          >
                            {m.enabled ? '停用' : '启用'}
                          </button>
                        </>
                      )}
                      {canRemove && (
                        <button
                          onClick={() => void handleDelete(m)}
                          className="text-[12px] text-[#b91c1c] bg-transparent border-none cursor-pointer p-0 hover:underline"
                        >
                          删除
                        </button>
                      )}
                    </span>
                  </td>
                </tr>
              )
            })}
            {visible.length === 0 && (
              <tr>
                <td colSpan={11} className="px-4 py-6 text-center text-[13px] text-[#94a3b8]">
                  {models.length === 0 ? '还没有配置模型' : '没有匹配的模型'}
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      {/* 连接测试结果：失败时端点原文可能有几百字，写成面板而不是 toast */}
      {testResult && (
        <div className={`border rounded-lg p-3.5 text-[12px] leading-relaxed ${
          testResult.result.ok
            ? 'border-[#a7f3d0] bg-[#f0fdf4] text-[#047857]'
            : 'border-[#fecaca] bg-[#fef2f2] text-[#b91c1c]'
        }`}>
          <div className="flex items-start justify-between gap-3">
            <div className="min-w-0">
              <div className="font-medium mb-1">
                连接测试 · {testResult.key} ·{' '}
                {testResult.result.ok
                  ? `成功（HTTP ${testResult.result.status}）`
                  : `失败${testResult.result.status ? `（HTTP ${testResult.result.status}）` : ''}`}
              </div>
              {testResult.result.ok ? (
                <div className="text-[#065f46]">
                  模型 <span className="font-mono">{testResult.result.model_api_name}</span> 回了：
                  <span className="font-mono ml-1">{testResult.result.sample || '（无文本输出）'}</span>
                  {testResult.result.stop_reason && (
                    <span className="text-[#94a3b8] ml-1">（{testResult.result.stop_reason}）</span>
                  )}
                </div>
              ) : (
                <pre className="whitespace-pre-wrap break-all font-mono text-[11px] m-0">
                  {testResult.result.detail}
                </pre>
              )}
            </div>
            <button
              onClick={() => setTestResult(null)}
              className="text-[#94a3b8] hover:text-[#0f172a] cursor-pointer shrink-0"
            >
              <svg className="w-3.5 h-3.5" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="2"><path d="M4 4l8 8M12 4l-8 8"/></svg>
            </button>
          </div>
        </div>
      )}

      {readOnly && (
        <div className="text-[12px] text-[#94a3b8]">
          你只能查看模型清单，没有增删改与连接测试的权限点。
        </div>
      )}

      {formOpen && (
        <ModelForm
          editing={editing}
          onClose={() => { setFormOpen(false); setEditing(null) }}
          onSubmit={handleSubmit}
        />
      )}
    </div>
  )
}
