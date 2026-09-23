import { useEffect, useRef, useState } from 'react'
import {
  fetchRoles, fetchPermissions, fetchRolePermissions,
  grantRolePermissions, setRoleDataScope,
  type RbacRole, type RbacPermission
} from '../../services/api'
import { useAuthStore, usePermi } from '../../stores/authStore'
import { Permi } from '../Permi'
import { showToast } from '../../utils/toast'

/** 这几个角色的"拥有全集"由服务端短路实现（doc 19-5.1），库里没有授权行。
 *  所以它们的列只读恒勾选 —— 让它可编辑会误导：改了也不生效。
 *  与 `server/authz/catalog.py` 的 `ALL_PERMS_ROLE_KEYS` 必须一致。
 *  它们之间只差数据范围（超管 ALL / 管理员 DEPT），那个仍可编辑。 */
const ALL_PERMS_ROLE_KEYS = new Set(['admin', 'dept_admin'])

const isAllPermsRole = (roleKey: string) => ALL_PERMS_ROLE_KEYS.has(roleKey)

/** 数据范围三档，按宽到窄列。取值必须与 `server/authz/service.py` 的
 *  `SCOPE_ORDER` 一致 —— 服务端按这个序取最宽的一档。 */
const DATA_SCOPES = [
  { value: 'ALL', label: '全部数据' },
  { value: 'DEPT', label: '部门数据' },
  { value: 'SELF', label: '仅本人数据' }
]

interface Row extends RbacPermission {
  depth: number
  /** 自己 + 全部后代的 id；父行勾选要连带子孙 */
  subtree: number[]
  /** 从根到这里（不含自己）的 id 链；任一祖先折叠了 => 本行不渲染 */
  ancestors: number[]
}

/** 扁平权限表 → 深度优先的行序列（父行紧跟着它的子孙）。 */
function buildRows(perms: RbacPermission[]): Row[] {
  const childrenOf = new Map<number, RbacPermission[]>()
  for (const p of perms) {
    const list = childrenOf.get(p.parent_id) ?? []
    list.push(p)
    childrenOf.set(p.parent_id, list)
  }
  for (const list of childrenOf.values()) {
    list.sort((a, b) => a.order_num - b.order_num || a.id - b.id)
  }

  const rows: Row[] = []
  const walk = (parentId: number, depth: number, ancestors: number[]): number[] => {
    const subtree: number[] = []
    for (const p of childrenOf.get(parentId) ?? []) {
      // 先占位、后回填：子节点要先算完才知道父行的 subtree 有多大
      const row: Row = { ...p, depth, subtree: [], ancestors }
      rows.push(row)
      const kids = walk(p.id, depth + 1, [...ancestors, p.id])
      row.subtree = [p.id, ...kids]
      subtree.push(...row.subtree)
    }
    return subtree
  }
  walk(0, 0, [])
  return rows
}

function rowState(row: Row, selected: Set<number>): 'checked' | 'unchecked' | 'indeterminate' {
  const hit = row.subtree.filter((id) => selected.has(id)).length
  if (hit === 0) return 'unchecked'
  return hit === row.subtree.length ? 'checked' : 'indeterminate'
}

export function RbacConfig() {
  // 改数据范围走的是另一个权限点（system:role:edit），勾选与它各管各的
  const canEditScope = usePermi('system:role:edit')
  const [roles, setRoles] = useState<RbacRole[]>([])
  const [perms, setPerms] = useState<RbacPermission[]>([])
  /** 服务端已保存的授权，脏标记对着它比 */
  const [saved, setSaved] = useState<Record<number, number[]>>({})
  const [draft, setDraft] = useState<Record<number, Set<number>>>({})
  const [scopeDraft, setScopeDraft] = useState<Record<number, string>>({})
  /** **展开**着的父行 id；空集 = 全部折叠（默认）。
   *  存「展开的」而不是「折叠的」，这样初值不用等 `perms` 异步回来才算得出来。 */
  const [expanded, setExpanded] = useState<Set<number>>(new Set())

  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const fetchedRef = useRef(false)

  useEffect(() => {
    if (fetchedRef.current) return
    fetchedRef.current = true
    void load()
  }, [])

  const load = async () => {
    setLoading(true)
    try {
      const [roleRes, permRes] = await Promise.all([fetchRoles(), fetchPermissions()])
      setRoles(roleRes.roles)
      setPerms(permRes.permissions)

      // 每个角色单独取授权；短路角色没有授权行，跳过（它们的列只读恒勾选）
      const grantPairs = await Promise.all(
        roleRes.roles
          .filter((r) => !isAllPermsRole(r.role_key))
          .map(async (r) => [r.id, (await fetchRolePermissions(r.id)).permission_ids] as const)
      )
      const savedGrants: Record<number, number[]> = {}
      const draftGrants: Record<number, Set<number>> = {}
      for (const [roleId, ids] of grantPairs) {
        savedGrants[roleId] = ids
        draftGrants[roleId] = new Set(ids)
      }
      setSaved(savedGrants)
      setDraft(draftGrants)
      setScopeDraft(Object.fromEntries(roleRes.roles.map((r) => [r.id, r.data_scope])))
    } catch (err) {
      showToast(err instanceof Error ? err.message : '权限配置加载失败')
    } finally {
      setLoading(false)
    }
  }

  const rows = buildRows(perms)
  /** 祖先里有没展开的就不渲染；顶层行 ancestors 为空，永远可见 */
  const visibleRows = rows.filter((r) => r.ancestors.every((id) => expanded.has(id)))

  const toggleExpand = (id: number) => {
    setExpanded((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }

  const changedRoles = roles.filter((r) => {
    // 短路角色只脏在数据范围上 —— 它没有授权行可改
    if (isAllPermsRole(r.role_key)) return scopeDraft[r.id] !== r.data_scope
    const now = draft[r.id]
    if (!now || scopeDraft[r.id] !== r.data_scope) return true
    const was = saved[r.id] ?? []
    return now.size !== was.length || was.some((id) => !now.has(id))
  })
  const dirty = changedRoles.length > 0

  const toggle = (roleId: number, row: Row) => {
    setDraft((prev) => {
      const next = new Set(prev[roleId] ?? [])
      if (rowState(row, next) === 'checked') {
        row.subtree.forEach((id) => next.delete(id))
      } else {
        row.subtree.forEach((id) => next.add(id))
      }
      return { ...prev, [roleId]: next }
    })
  }

  const handleSave = async () => {
    setSaving(true)
    try {
      for (const role of changedRoles) {
        if (role.data_scope !== scopeDraft[role.id]) {
          await setRoleDataScope(role.id, scopeDraft[role.id])
        }
        // 短路角色的授权行不存在也不可写，只提交数据范围
        if (!isAllPermsRole(role.role_key)) {
          const ids = [...(draft[role.id] ?? [])]
          await grantRolePermissions(role.id, ids)
        }
      }
      showToast('已保存')
      // 自己可能就在被改的角色里（改的是自己那一档），重拉一次免得侧边栏与后端不一致
      await useAuthStore.getState().refreshPermissions()
      await load()
    } catch (err) {
      showToast(err instanceof Error ? err.message : '保存失败')
    } finally {
      setSaving(false)
    }
  }

  if (loading) {
    return <div className="text-[13px] text-[#64748b]">加载中…</div>
  }
  if (rows.length === 0) {
    return <div className="text-[13px] text-[#64748b]">权限点清单为空，检查服务端是否已完成 catalog 对账。</div>
  }

  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-start gap-3">
        <div className="flex-1 text-[12px] text-[#64748b] leading-relaxed">
          勾选即授权，父行连带其下全部子项。超管与管理员两列只读恒勾选 ——
          它们在服务端短路为全集，以后新增权限点自动拥有，两者只差一个数据范围。
          保存只提交有改动的角色。
        </div>
        <div className="flex items-center gap-2 flex-shrink-0">
          <Permi perms="system:role:grant">
            <button
              onClick={handleSave}
              disabled={!dirty || saving}
              className="px-4 py-1.5 rounded-md bg-[#0f172a] text-white text-[13px] font-medium cursor-pointer disabled:opacity-40 disabled:cursor-not-allowed hover:bg-[#334155]"
            >
              {saving ? '保存中…' : '保存'}
            </button>
          </Permi>
        </div>
      </div>

      <div className="overflow-auto border border-[#e2e8f0] rounded-lg bg-white">
        <table className="border-collapse w-full min-w-[720px]">
          <thead>
            <tr className="bg-[#f8fafc] sticky top-0 z-10">
              <th className="text-left text-[12px] font-semibold text-[#334155] px-4 py-2.5 border-b border-[#e2e8f0]">
                权限点
              </th>
              {roles.map((role) => (
                <th
                  key={role.id}
                  className="text-center text-[12px] font-semibold text-[#334155] px-4 py-2.5 border-b border-l border-[#e2e8f0] w-[200px]"
                >
                  <div>{role.role_name}</div>
                  <div className="font-normal text-[11px] text-[#94a3b8] mt-0.5">{role.role_key}</div>
                  <select
                    value={scopeDraft[role.id] ?? role.data_scope}
                    disabled={!canEditScope}
                    onChange={(e) => setScopeDraft((p) => ({ ...p, [role.id]: e.target.value }))}
                    className="mt-1.5 w-full text-[11px] font-normal border border-[#e2e8f0] rounded px-1.5 py-1 bg-white text-[#334155] cursor-pointer disabled:opacity-50 disabled:cursor-not-allowed"
                    title="数据范围：全部数据 / 本部门及下级 / 仅本人"
                  >
                    {DATA_SCOPES.map((s) => (
                      <option key={s.value} value={s.value}>{s.label}</option>
                    ))}
                  </select>
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {visibleRows.map((row) => (
              <tr key={row.id} className="hover:bg-[#f8fafc]">
                {/* 整格可点即展开：只有 4px 的三角能点太难点中。只挂名字这一格，
                    右面的勾选格是各自独立的 td，点它们不该连带折叠。 */}
                <td
                  className={`px-4 py-2 border-b border-[#f1f5f9] ${
                    row.subtree.length > 1 ? 'cursor-pointer' : ''
                  }`}
                  onClick={row.subtree.length > 1 ? () => toggleExpand(row.id) : undefined}
                >
                  <div
                    className="flex items-center gap-2"
                    style={{ paddingLeft: `${row.depth * 18}px` }}
                  >
                    {row.subtree.length > 1 ? (
                      // 纯视觉：点击冒泡到上面整格的 onClick，这里不再自己切一次（会等于没切）
                      <span
                        title={expanded.has(row.id) ? '折叠' : '展开'}
                        className="w-4 h-4 flex items-center justify-center flex-shrink-0 text-[11px] text-[#94a3b8]"
                      >
                        {expanded.has(row.id) ? '▾' : '▸'}
                      </span>
                    ) : (
                      <span className="w-4 flex-shrink-0" />
                    )}
                    <span
                      className={
                        row.permission_type === 'M'
                          ? 'text-[13px] font-semibold text-[#0f172a]'
                          : 'text-[13px] text-[#334155]'
                      }
                    >
                      {row.permission_name}
                    </span>
                    {row.perms && (
                      <span className="text-[11px] text-[#94a3b8] font-mono">{row.perms}</span>
                    )}
                  </div>
                </td>
                {roles.map((role) => {
                  const locked = isAllPermsRole(role.role_key)
                  const state = locked
                    ? 'checked'
                    : rowState(row, draft[role.id] ?? new Set<number>())
                  return (
                    <td
                      key={role.id}
                      className="text-center px-4 py-2 border-b border-l border-[#f1f5f9]"
                    >
                      <TriCheckbox
                        state={state}
                        disabled={locked}
                        title={locked ? `${role.role_key} 短路为全集，不可编辑` : undefined}
                        onChange={() => toggle(role.id, row)}
                      />
                    </td>
                  )
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}

/** 原生 checkbox 的半选态只能通过 ref 设 `indeterminate`，Tailwind 表达不了。 */
function TriCheckbox({
  state, disabled, title, onChange
}: {
  state: 'checked' | 'unchecked' | 'indeterminate'
  disabled?: boolean
  title?: string
  onChange: () => void
}) {
  const ref = useRef<HTMLInputElement>(null)
  useEffect(() => {
    if (ref.current) ref.current.indeterminate = state === 'indeterminate'
  }, [state])

  return (
    <input
      ref={ref}
      type="checkbox"
      checked={state === 'checked'}
      disabled={disabled}
      title={title}
      onChange={onChange}
      className={`w-4 h-4 accent-[#047857] ${disabled ? 'cursor-not-allowed opacity-60' : 'cursor-pointer'}`}
    />
  )
}
