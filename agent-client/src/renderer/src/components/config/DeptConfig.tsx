import { useEffect, useRef, useState } from 'react'
import {
  fetchDepts, fetchUsers, fetchRoles, createDept, createUser, updateDept, deleteDept,
  setUserDept, setUserRoles,
  type DeptNode, type DeptUser, type RbacRole
} from '../../services/api'
import { usePermi, useAuthStore } from '../../stores/authStore'
import { showToast } from '../../utils/toast'
import { buildDeptRows, deptLabel, type DeptRow } from './deptTree'
import { CreateUserModal, UserDeptSelect, UserRoleSelect } from './UserForm'

/** 默认部门的业务键。它不可删（服务端也拦），它是所有人的兜底归属。 */
const DEFAULT_DEPT_KEY = 'default'

/** 选中的节点：某个部门，或「未分配」那一组。
 *  「未分配」不是真部门，只是一堆 `dept_id IS NULL` 的人 —— 给它一个合成节点，
 *  否则这些人不出现在任何节点下，就成了隐形人。 */
type Selection = number | 'unassigned'

/** 树上的行内输入框在编什么。
 *
 *  **不能用 `window.prompt()`**：Electron 不实现它，28+ 起直接同步返回 `null`
 *  且不弹任何东西 —— 写成 `prompt(...)` 再判 null 的分支会静默失效（点了没反应、
 *  也不报错）。同一页的 `confirm()` 没事，那个 Electron 有实现。 */
type Editing =
  | { mode: 'addRoot' }
  | { mode: 'addChild'; parentId: number }
  | { mode: 'rename'; deptId: number }

export function DeptConfig() {
  // 本页一个控件一个点（doc 19-4.2 表 29–36 行）：顶级与子部门是两点、同一支 API
  const canAddRoot = usePermi('system:dept:addRoot')
  const canAddChild = usePermi('system:dept:addChild')
  const canEdit = usePermi('system:dept:edit')
  const canRemove = usePermi('system:dept:remove')
  const canAssign = usePermi('system:dept:assign')
  /** 建号（doc 19-4.2）：本页那个「添加用户」有自己的点，`system:user:add`
   *  是用户管理页的「新增」——同一件事，两点任一即可。
   *  超管可任意部门、部门管理员只在本部门及下级，这道范围由服务端判，
   *  前端只决定那个菜单项给不给看。 */
  const canCreateUser = usePermi(['system:dept:addUser', 'system:user:add'])
  /** 行尾「⋯」里有没有东西可放 —— 都没有就不给按钮（空菜单没意义） */
  const hasRowActions = canAddChild || canEdit || canRemove || canCreateUser

  /** 角色列要读角色名，那要 `system:role:list` —— 和本页入口 `client:dept:config`
   *  不相干。没这个点就整列不渲染，并且**不能**去拉 `/role/list`（一拉就 403）。 */
  const canSeeRoles = usePermi('system:role:list')
  const canAssignRole = usePermi('system:role:assign')

  const [depts, setDepts] = useState<DeptNode[]>([])
  const [users, setUsers] = useState<DeptUser[]>([])
  const [roles, setRoles] = useState<RbacRole[]>([])
  /** 正在提交角色的那个人 —— 请求在飞时禁掉他那行的 chip，免得连点打架 */
  const [roleBusy, setRoleBusy] = useState<string | null>(null)
  /** **展开**着的父行 id；空集 = 全部折叠（默认） */
  const [expanded, setExpanded] = useState<Set<number>>(new Set())
  const [selected, setSelected] = useState<Selection | null>(null)

  const [editing, setEditing] = useState<Editing | null>(null)
  const [draft, setDraft] = useState('')

  /** 行尾「⋯」菜单：开在哪个部门上，以及它的 fixed 坐标。
   *  必须是 `fixed`：左树是 `overflow-y-auto`，行内 `absolute` 会被那个滚动容器
   *  裁掉（一个轴非 visible 时另一个会算成 auto），越靠底部越明显。 */
  const [menu, setMenu] = useState<{ deptId: number; left: number; top: number } | null>(null)
  const menuRef = useRef<HTMLDivElement | null>(null)

  /** 「添加用户」弹窗开着时，人要落进哪个部门 */
  const [createDeptId, setCreateDeptId] = useState<number | null>(null)

  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)
  const fetchedRef = useRef(false)

  useEffect(() => {
    if (fetchedRef.current) return
    fetchedRef.current = true
    void load()
  }, [])

  /** 菜单开着时：点别处、按 Esc 都关。
   *  开菜单那一次点击在按钮上 `stopPropagation` 过了，到不了 document，
   *  所以不需要侧栏那种 `setTimeout(..., 10)` 的绕法。 */
  useEffect(() => {
    if (!menu) return
    const onDown = (e: MouseEvent) => {
      // 菜单项自己的点击由它的 onClick 处理（顺手关掉），这里别抢
      if (menuRef.current?.contains(e.target as Node)) return
      setMenu(null)
    }
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setMenu(null)
    }
    document.addEventListener('mousedown', onDown)
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('mousedown', onDown)
      document.removeEventListener('keydown', onKey)
    }
  }, [menu])

  const load = async () => {
    setLoading(true)
    try {
      const [deptRes, userRes, roleRes] = await Promise.all([
        fetchDepts(),
        fetchUsers(),
        // 没权限就不发这个请求 —— 403 会让整页加载失败，而角色列本来就不显示
        canSeeRoles ? fetchRoles() : Promise.resolve({ roles: [] as RbacRole[] })
      ])
      setDepts(deptRes.depts)
      setUsers(userRes.users)
      setRoles(roleRes.roles)
      // 首次进来落到默认部门上：那是所有人都在的地方，比空选好看
      setSelected((prev) => {
        if (prev !== null) return prev
        const fallback = deptRes.depts.find((d) => d.dept_key === DEFAULT_DEPT_KEY)
        return fallback?.id ?? deptRes.depts[0]?.id ?? null
      })
    } catch (err) {
      showToast(err instanceof Error ? err.message : '部门配置加载失败')
    } finally {
      setLoading(false)
    }
  }

  const rows = buildDeptRows(depts)
  const deptById = new Map(depts.map((d) => [d.id, d]))
  const visibleRows = rows.filter((r) => r.ancestors.every((id) => expanded.has(id)))
  const unassigned = users.filter((u) => u.dept_id == null)
  const current = typeof selected === 'number' ? deptById.get(selected) ?? null : null

  /** 树行上那个数字：**含下级**的用户总数（原来只算直属，看不出一个部门带头带了多少人）。
   *  两个 Map 各扫一遍即可，别在 JSX 里对每行再 filter 一遍 users。 */
  const directCount = new Map<number, number>()
  for (const u of users) {
    if (u.dept_id == null) continue
    directCount.set(u.dept_id, (directCount.get(u.dept_id) ?? 0) + 1)
  }
  const subtreeCount = new Map<number, number>()
  for (const r of rows) {
    let n = 0
    for (const id of r.subtree) n += directCount.get(id) ?? 0
    subtreeCount.set(r.id, n)
  }

  const toggleExpand = (id: number) => {
    setExpanded((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }

  /** 挪部门下拉的选项。行序即深度优先序，缩进同左树。 */
  const options = rows.map((row) => ({ id: row.id, label: deptLabel(row) }))

  const members =
    selected === 'unassigned'
      ? unassigned
      : typeof selected === 'number'
        ? users.filter((u) => u.dept_id === selected)
        : []

  /** 建顶级部门（`parent_id = 0`）—— 与「默认部门」平级。
   *  只有子部门入口时，新部门必然挂在选中节点下，从空库起永远长不出平级的一层。 */
  const startAddRoot = () => {
    setDraft('')
    setEditing({ mode: 'addRoot' })
  }

  /** 在某个部门下建子部门（行尾「⋯」菜单）。 */
  const startAddChild = (parentId: number) => {
    setDraft('')
    setEditing({ mode: 'addChild', parentId })
  }

  /** 改名（行尾「⋯」菜单）—— 输入框就长在这一行上。 */
  const startRename = (deptId: number) => {
    const dept = deptById.get(deptId)
    if (!dept) return
    setDraft(dept.dept_name)
    setEditing({ mode: 'rename', deptId })
  }

  const cancelEdit = () => {
    setEditing(null)
    setDraft('')
  }

  /** 三个创建/改名分支的公共出口。失败**不关**输入框 —— 名字被服务端拒了
   *  （重名、上级没了）还能改一改再提交，别让人重敲一遍。 */
  const commitEdit = async () => {
    if (!editing) return
    const name = draft.trim()
    if (!name) {
      showToast('名称不能为空')
      return
    }
    setBusy(true)
    try {
      if (editing.mode === 'rename') {
        const dept = deptById.get(editing.deptId)
        if (!dept) return
        await updateDept(dept.id, dept.parent_id, name, dept.order_num)
        showToast('已保存')
        await load()
      } else {
        const parentId = editing.mode === 'addRoot' ? 0 : editing.parentId
        const { id } = await createDept(parentId, name)
        if (parentId !== 0) setExpanded((prev) => new Set(prev).add(parentId))
        showToast('已新建')
        await load()
        setSelected(id)
      }
      setEditing(null)
      setDraft('')
    } catch (err) {
      showToast(err instanceof Error ? err.message : '操作失败')
    } finally {
      setBusy(false)
    }
  }

  /** 部门行：展开箭头 + 名字 + 成员数 + 「⋯」。
   *  操作挂在行上而不是右栏，就不用先选中父行才能给它加子部门。
   *  改名时整行换成输入框（缩进不动）。
   *  **整行可点即展开**：只有那个 4px 的三角能点太难点中，所以点行 = 选中 + 切展开。 */
  const deptRow = (row: DeptRow) => {
    const expandable = row.subtree.length > 1
    const total = subtreeCount.get(row.id) ?? 0
    const direct = directCount.get(row.id) ?? 0
    return (
      <div
        onClick={() => {
          setSelected(row.id)
          // 叶子行只选中；有下级的连展开一起切，省得去瞄那个三角
          if (expandable) toggleExpand(row.id)
        }}
        className={`flex items-center gap-1.5 py-1.5 pr-3 text-[13px] cursor-pointer ${
          selected === row.id
            ? 'bg-[rgba(167,243,208,0.4)] text-[#047857] font-medium'
            : 'text-[#334155] hover:bg-[#f8fafc]'
        }`}
        style={{ paddingLeft: `${8 + row.depth * 16}px` }}
      >
        {expandable ? (
          // 纯视觉：点击冒泡到上面整行的 onClick，这里不再自己切一次（会等于没切）
          <span
            title={expanded.has(row.id) ? '折叠' : '展开'}
            className="w-4 h-4 flex items-center justify-center flex-shrink-0 text-[11px] text-[#94a3b8]"
          >
            {expanded.has(row.id) ? '▾' : '▸'}
          </span>
        ) : (
          <span className="w-4 flex-shrink-0" />
        )}
        <span className="flex-1 overflow-hidden text-ellipsis whitespace-nowrap">{row.dept_name}</span>
        <span
          className="text-[11px] text-[#94a3b8] flex-shrink-0"
          title={total === direct ? `共 ${total} 人` : `含下级共 ${total} 人（直属 ${direct}）`}
        >
          {total}
        </span>
        {hasRowActions && (
          <button
            onClick={(e) => {
              // 两个作用：不选中这一行；不让这次点击冒到 document 把菜单立刻关掉
              e.stopPropagation()
              const r = e.currentTarget.getBoundingClientRect()
              setMenu({ deptId: row.id, left: r.left, top: r.bottom + 4 })
            }}
            title="更多操作"
            className="w-5 h-5 flex items-center justify-center flex-shrink-0 p-0 border-none bg-none rounded cursor-pointer text-[#94a3b8] hover:text-[#0f172a] hover:bg-[#f1f5f9]"
          >
            <svg className="w-4 h-4" viewBox="0 0 16 16" fill="currentColor">
              <circle cx="3" cy="8" r="1.3" /><circle cx="8" cy="8" r="1.3" /><circle cx="13" cy="8" r="1.3" />
            </svg>
          </button>
        )}
      </div>
    )
  }

  /** 树上的输入行：缩进到 `depth`，Enter 提交、Esc 取消。 */
  const editRow = (depth: number) => (
    <div
      className="flex items-center gap-1.5 py-1 pr-3 bg-[#f0fdf4]"
      style={{ paddingLeft: `${8 + depth * 16}px` }}
    >
      <span className="w-4 flex-shrink-0" />
      <input
        autoFocus
        value={draft}
        onChange={(e) => setDraft(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === 'Enter') void commitEdit()
          else if (e.key === 'Escape') cancelEdit()
        }}
        placeholder="部门名称"
        disabled={busy}
        className="flex-1 min-w-0 text-[13px] border border-[#a7f3d0] rounded px-1.5 py-0.5 bg-white text-[#0f172a] outline-none"
      />
      <button
        onClick={() => void commitEdit()}
        disabled={busy}
        className="px-2 py-0.5 rounded border border-[#a7f3d0] bg-white text-[11px] text-[#047857] cursor-pointer disabled:opacity-40 hover:bg-[#f1f5f9]"
      >
        确定
      </button>
      <button
        onClick={cancelEdit}
        disabled={busy}
        className="px-2 py-0.5 rounded border border-[#e2e8f0] bg-white text-[11px] text-[#64748b] cursor-pointer disabled:opacity-40 hover:bg-[#f1f5f9]"
      >
        取消
      </button>
    </div>
  )

  const handleDelete = async (deptId: number) => {
    const dept = deptById.get(deptId)
    if (!dept) return
    if (!confirm(`删除部门「${dept.dept_name}」？有下级部门或还有成员时不会删掉。`)) return
    setBusy(true)
    try {
      await deleteDept(dept.id)
      // 删的可能不是当前选中的那个，只有真删掉了选中的才清空
      setSelected((prev) => (prev === dept.id ? null : prev))
      showToast('已删除')
      await load()
    } catch (err) {
      showToast(err instanceof Error ? err.message : '删除失败')
    } finally {
      setBusy(false)
    }
  }

  /** 写完之后只重拉用户行（不整页 `load()` —— 树和角色名都没动）。 */
  const reloadUsers = async () => {
    try {
      const res = await fetchUsers()
      setUsers(res.users)
    } catch {
      /* 重拉失败就保持现状，下次操作会再对齐一次 */
    }
  }

  /** 建号。返回 false 时弹窗不关（见 `CreateUserModal`）—— 用户名被占、密码不合规、
   *  角色不可授都能改一改再交；错误文案已由 `showToast` 给出。 */
  const handleCreateUser = async (
    deptId: number,
    username: string,
    password: string,
    roleId: number | null,
  ): Promise<boolean> => {
    try {
      await createUser(username, password, deptId, roleId)
      showToast('已创建')
      await reloadUsers()
      // 新人不一定落在当前选中的部门，切过去更直观
      setSelected(deptId)
      return true
    } catch (err) {
      showToast(err instanceof Error ? err.message : '新建用户失败')
      return false
    }
  }

  const handleMove = async (userId: string, deptId: number) => {
    try {
      await setUserDept(userId, deptId)
      showToast('已调整')
      await reloadUsers()
    } catch (err) {
      showToast(err instanceof Error ? err.message : '调整失败')
    }
  }

  /** 换一个角色。服务端是整集替换，单选就是把新值包成单元素数组递过去。 */
  const handleRoleSet = async (userId: string, roleId: number) => {
    setRoleBusy(userId)
    try {
      await setUserRoles(userId, [roleId])
      showToast('已保存')
      await reloadUsers()
      // 改的是自己就重拉 /auth/me —— 服务端已经清了自己的缓存（doc 19-5.1 的写穿），
      // 前端不跟着重拉，侧边栏入口和真实权限就对不上
      if (userId === useAuthStore.getState().user?.id) {
        void useAuthStore.getState().refreshPermissions()
      }
    } catch (err) {
      showToast(err instanceof Error ? err.message : '角色保存失败')
      // 服务端拒绝时什么都没改，重拉把这一格显示回实际值
      await reloadUsers()
    } finally {
      setRoleBusy(null)
    }
  }

  if (loading) {
    return <div className="text-[13px] text-[#64748b]">加载中…</div>
  }
  if (rows.length === 0) {
    return <div className="text-[13px] text-[#64748b]">部门清单为空，检查服务端是否已完成 seed_dept。</div>
  }

  return (
    <div className="flex flex-col gap-4 h-full">
      <div className="text-[12px] text-[#64748b] leading-relaxed">
        后台不建组织架构时，所有人都落在「默认部门」里。行尾「⋯」添加用户 / 加子部门 / 改名 / 删这个部门；
        右侧两个下拉：一个挪部门、一个换角色。删除只在没下级、没成员时生效。
      </div>

      <div className="flex gap-4 flex-1 min-h-0">
        {/* ── 左：部门树 ── */}
        <div className="w-[280px] min-w-[280px] flex flex-col border border-[#e2e8f0] rounded-lg bg-white overflow-hidden">
          <div className="flex items-center justify-between px-3 py-2 border-b border-[#e2e8f0] bg-[#f8fafc]">
            <span className="text-[12px] font-semibold text-[#334155]">部门</span>
            {canAddRoot && (
              <button
                onClick={startAddRoot}
                disabled={busy}
                className="px-2 py-0.5 rounded border border-[#e2e8f0] bg-white text-[11px] text-[#334155] cursor-pointer disabled:opacity-40 hover:bg-[#f1f5f9]"
              >
                新建顶级部门
              </button>
            )}
          </div>
          <div className="flex-1 overflow-y-auto py-1">
            {editing?.mode === 'addRoot' && editRow(0)}
            {visibleRows.map((row) => (
              <div key={row.id}>
                {editing?.mode === 'rename' && editing.deptId === row.id
                  ? editRow(row.depth)
                  : deptRow(row)}
                {/* 子部门的输入框贴着父行放 —— 缩进就是「会挂在哪」的答案 */}
                {editing?.mode === 'addChild' && editing.parentId === row.id && editRow(row.depth + 1)}
              </div>
            ))}
            {unassigned.length > 0 && (
              <div
                onClick={() => setSelected('unassigned')}
                className={`flex items-center gap-1.5 py-1.5 pr-3 pl-2 border-t border-[#f1f5f9] mt-1 text-[13px] cursor-pointer ${
                  selected === 'unassigned'
                    ? 'bg-[rgba(167,243,208,0.4)] text-[#047857] font-medium'
                    : 'text-[#b45309] hover:bg-[#f8fafc]'
                }`}
              >
                <span className="w-4 flex-shrink-0" />
                <span className="flex-1">未分配</span>
                <span className="text-[11px] text-[#94a3b8] flex-shrink-0">{unassigned.length}</span>
              </div>
            )}
          </div>
        </div>

        {/* ── 右：成员 ── */}
        <div className="flex-1 flex flex-col border border-[#e2e8f0] rounded-lg bg-white overflow-hidden">
          <div className="flex items-center justify-between gap-3 px-3 py-2 border-b border-[#e2e8f0] bg-[#f8fafc]">
            <span className="text-[12px] font-semibold text-[#334155]">
              {selected === 'unassigned' ? '未分配成员' : current ? `${current.dept_name} 的成员` : '成员'}
              <span className="font-normal text-[#94a3b8] ml-2">{members.length} 人</span>
            </span>
          </div>
          <div className="flex-1 overflow-auto">
            <table className="border-collapse w-full">
              <thead>
                <tr className="bg-[#f8fafc] sticky top-0">
                  <th className="text-left text-[12px] font-semibold text-[#334155] px-4 py-2 border-b border-[#e2e8f0]">用户</th>
                  <th className="text-left text-[12px] font-semibold text-[#334155] px-4 py-2 border-b border-[#e2e8f0] w-[220px]">所属部门</th>
                  {canSeeRoles && (
                    <th className="text-left text-[12px] font-semibold text-[#334155] px-4 py-2 border-b border-[#e2e8f0] w-[240px]">角色</th>
                  )}
                </tr>
              </thead>
              <tbody>
                {members.map((u) => (
                  <tr key={u.id} className="hover:bg-[#f8fafc]">
                    <td className="px-4 py-2 border-b border-[#f1f5f9] text-[13px] text-[#334155]">
                      {u.display_name || u.username}
                      <span className="text-[11px] text-[#94a3b8] font-mono ml-2">{u.username}</span>
                    </td>
                    <td className="px-4 py-2 border-b border-[#f1f5f9]">
                      <UserDeptSelect
                        value={u.dept_id}
                        options={options}
                        disabled={!canAssign}
                        onChange={(deptId) => void handleMove(u.id, deptId)}
                      />
                    </td>
                    {canSeeRoles && (
                      <td className="px-4 py-2 border-b border-[#f1f5f9]">
                        <UserRoleSelect
                          // 单选只认第一个；服务端历史上可能挂过多个，这里只显示其中一个
                          value={(u.role_ids ?? [])[0] ?? null}
                          roles={roles}
                          disabled={!canAssignRole || roleBusy === u.id}
                          // 没传 emptyLabel，空值项是禁着的 —— null 到不了这里，只为类型收敛
                          onChange={(roleId) => { if (roleId !== null) void handleRoleSet(u.id, roleId) }}
                        />
                      </td>
                    )}
                  </tr>
                ))}
                {members.length === 0 && (
                  <tr>
                    <td colSpan={canSeeRoles ? 3 : 2} className="px-4 py-6 text-center text-[13px] text-[#94a3b8]">
                      这个部门下还没有人
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        </div>
      </div>

      {/* 只读管理员看得到页面，但一个写按钮都不该出现 */}
      {!canAddRoot && !canAddChild && !canEdit && !canRemove && !canAssign && !canCreateUser && (
        <div className="text-[12px] text-[#94a3b8]">
          你只能查看部门结构，没有增删改的权限点。
        </div>
      )}

      {/* 「添加用户」弹窗。目标部门在菜单点开时就定下，弹窗里只读显示
          —— 本页的部门由「在哪个部门上点的」决定，用户管理页才是自己挑。 */}
      {createDeptId !== null && (
        <CreateUserModal
          pick={{ lockedId: createDeptId, lockedName: deptById.get(createDeptId)?.dept_name ?? '' }}
          // 角色那一项与用户管理页同一个条件：要挂角色得先看得见角色名
          roles={canAssignRole && canSeeRoles ? roles : undefined}
          onClose={() => setCreateDeptId(null)}
          onSubmit={
            (username, password, deptId, roleId) =>
              handleCreateUser(deptId, username, password, roleId)
          }
        />
      )}

      {/* 行尾「⋯」菜单。`fixed` 定位到按钮正下方，不受左树滚动容器裁剪。
          不复用 AppLayout 的 `#ctxMenu`：那是 innerHTML + 手动 addEventListener 的
          写法，两边共用一个节点会互相踩。这里 React 自己控制生死。 */}
      {menu && (
        <div
          ref={menuRef}
          className="fixed bg-white border border-[#e2e8f0] rounded-[10px] shadow-lg min-w-[160px] p-1 z-[200]"
          style={{ left: menu.left, top: menu.top }}
        >
          {canCreateUser && (
            <div
              className="ctx-menu-item"
              onClick={() => { setCreateDeptId(menu.deptId); setMenu(null) }}
            >
              添加用户
            </div>
          )}
          {canAddChild && (
            <div
              className="ctx-menu-item"
              onClick={() => { startAddChild(menu.deptId); setMenu(null) }}
            >
              新建子部门
            </div>
          )}
          {canEdit && (
            <div
              className="ctx-menu-item"
              onClick={() => { startRename(menu.deptId); setMenu(null) }}
            >
              改名
            </div>
          )}
          {canRemove && (
            deptById.get(menu.deptId)?.dept_key === DEFAULT_DEPT_KEY ? (
              // 留着但点不动 —— 比整项消失更能说明「为什么这里不能删」
              <div className="ctx-menu-item opacity-40 cursor-not-allowed" title="默认部门不可删除">
                删除
              </div>
            ) : (
              <div
                className="ctx-menu-item danger"
                onClick={() => { setMenu(null); void handleDelete(menu.deptId) }}
              >
                删除
              </div>
            )
          )}
        </div>
      )}
    </div>
  )
}
