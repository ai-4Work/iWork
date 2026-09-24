/** 「用户管理」页（docs/chapters/19-权限管理RBAC.md §4.2 表 2–7 行）。
 *
 *  和部门配置页是**两块界面、一套动作**：两页都能建号、改部门、换角色，走同一支 API、
 *  同一批权限点（见 `UserForm.tsx` 头注释）。部门配置页管组织架构（左树 + 成员表），
 *  这一页是全员平铺的一张大表 —— 封号、解锁、重置密码这些**跨部门**的账号操作在这里。
 *
 *  闸门一览（缺了各自怎样）：
 *  | 闸门 | 点 | 缺了 |
 *  | 列表本体 | `system:user:list` | 整页一句提示，一个请求不发 |
 *  | 部门列 | `system:dept:list` | 整列不渲染，且不拉 `/dept/list` |
 *  | 角色列 | `system:role:list` | 整列不渲染，且不拉 `/role/list` |
 *  | 新增用户 | `system:user:add` 且 `system:dept:list` | 不渲染（挑不出落地部门） |
 *  | 建号弹窗的角色项 | `system:role:assign` 且 `system:role:list` | 弹窗里没这一项，建出来的号是默认角色 |
 *  | 改名 / 重置密码 / 停用启用 / 解锁 | 各自一个点 | 各自不渲染 |
 *
 *  后三列是"读取闸门"（没有控件，只决定发不发请求）—— 与部门的 `canSeeRoles` 同一手法，
 *  不这么写会让 403 把整页加载拖垮。
 */
import { useEffect, useRef, useState } from 'react'
import {
  createUser, fetchDepts, fetchRoles, fetchUsers, resetUserPassword, setUserDept,
  setUserRoles, setUserStatus, unlockUser, updateUser,
  type DeptNode, type DeptUser, type RbacRole
} from '../../services/api'
import { useAuthStore, usePermi } from '../../stores/authStore'
import { showToast } from '../../utils/toast'
import { deptOptions } from './deptTree'
import {
  CreateUserModal, ResetPasswordModal, UserDeptSelect, UserRoleSelect, isLocked
} from './UserForm'

export function UserConfig() {
  // 一列/一个动作一个点（doc 19-4.2 表 2–7 行）。全都无条件调用，次序别动。
  const canList = usePermi('system:user:list')
  const canAddUser = usePermi('system:user:add')
  const canEdit = usePermi('system:user:edit')
  const canReset = usePermi('system:user:resetPwd')
  const canStatus = usePermi('system:user:status')
  const canUnlock = usePermi('system:user:unlock')
  /** 挪部门与换角色在部门配置页也有控件，共用这两个点（表 34–36 行） */
  const canAssign = usePermi('system:dept:assign')
  const canAssignRole = usePermi('system:role:assign')
  /** 读取闸门：列的存在本身要权限 —— 没有就整列不渲染、也不发那个请求 */
  const canSeeDepts = usePermi('system:dept:list')
  const canSeeRoles = usePermi('system:role:list')

  /** 建号要挑落地部门，挑不出就不给按钮 */
  const canCreate = canAddUser && canSeeDepts
  /** 建号弹窗里出不出「角色」那一项：写要点（`role:assign`），读要角色名（`role:list`） */
  const canGrantRole = canAssignRole && canSeeRoles

  const [users, setUsers] = useState<DeptUser[]>([])
  const [depts, setDepts] = useState<DeptNode[]>([])
  const [roles, setRoles] = useState<RbacRole[]>([])
  const [search, setSearch] = useState('')
  /** 正在提交角色的那个人 —— 请求在飞时禁掉他那行的下拉，免得连点打架 */
  const [roleBusy, setRoleBusy] = useState<string | null>(null)
  /** 正在行内改显示名的那个人 */
  const [editingId, setEditingId] = useState<string | null>(null)
  const [draft, setDraft] = useState('')

  const [createOpen, setCreateOpen] = useState(false)
  /** 重置密码弹窗对着谁 */
  const [pwdTarget, setPwdTarget] = useState<DeptUser | null>(null)

  const [loading, setLoading] = useState(true)
  const fetchedRef = useRef(false)

  useEffect(() => {
    if (!canList || fetchedRef.current) return
    fetchedRef.current = true
    void load()
  }, [canList])

  const load = async () => {
    setLoading(true)
    try {
      const [userRes, deptRes, roleRes] = await Promise.all([
        fetchUsers(),
        // 没权限就不发这个请求 —— 403 会让整页加载失败，而那一列本来就不显示
        canSeeDepts ? fetchDepts() : Promise.resolve({ depts: [] as DeptNode[] }),
        canSeeRoles ? fetchRoles() : Promise.resolve({ roles: [] as RbacRole[] })
      ])
      setUsers(userRes.users)
      setDepts(deptRes.depts)
      setRoles(roleRes.roles)
    } catch (err) {
      showToast(err instanceof Error ? err.message : '用户列表加载失败')
    } finally {
      setLoading(false)
    }
  }

  /** 写完之后只重拉用户行（部门树与角色名都没动） */
  const reloadUsers = async () => {
    try {
      const res = await fetchUsers()
      setUsers(res.users)
    } catch {
      /* 重拉失败就保持现状，下次操作会再对齐一次 */
    }
  }

  const options = deptOptions(depts)

  // 客户端过滤，不新增接口、也不新增权限点 —— 一页可见用户本来就全在手里了
  const q = search.trim().toLowerCase()
  const visible = q
    ? users.filter(
        (u) =>
          u.username.toLowerCase().includes(q) ||
          (u.display_name ?? '').toLowerCase().includes(q)
      )
    : users

  const handleCreate = async (
    username: string,
    password: string,
    deptId: number,
    roleId: number | null,
  ): Promise<boolean> => {
    try {
      await createUser(username, password, deptId, roleId)
      showToast('已创建')
      await reloadUsers()
      return true
    } catch (err) {
      showToast(err instanceof Error ? err.message : '新建用户失败')
      return false
    }
  }

  const handleRename = async (user: DeptUser) => {
    const name = draft.trim()
    if (!name) {
      showToast('显示名不能为空')
      return
    }
    try {
      await updateUser(user.id, name)
      showToast('已保存')
      setEditingId(null)
      setDraft('')
      await reloadUsers()
    } catch (err) {
      // 失败**不关**输入框 —— 改了再交，别让人重敲
      showToast(err instanceof Error ? err.message : '保存失败')
    }
  }

  const handleMove = async (userId: string, deptId: number) => {
    try {
      await setUserDept(userId, deptId)
      showToast('已调整')
      await reloadUsers()
    } catch (err) {
      showToast(err instanceof Error ? err.message : '调整失败')
      await reloadUsers()
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
      await reloadUsers()
    } finally {
      setRoleBusy(null)
    }
  }

  const handleReset = async (userId: string, password: string): Promise<boolean> => {
    try {
      await resetUserPassword(userId, password)
      showToast('已重置，他需要用新密码重新登录')
      await reloadUsers()
      return true
    } catch (err) {
      showToast(err instanceof Error ? err.message : '重置密码失败')
      return false
    }
  }

  /** 停用会顺手吊销那个人的全部 refresh token，所以问一句再动 —— 他会被踢下线。 */
  const handleStatus = async (user: DeptUser) => {
    const next = user.status === 'disabled' ? 'active' : 'disabled'
    if (next === 'disabled' && !confirm(`停用「${user.username}」？他会立即被踢下线。`)) return
    try {
      await setUserStatus(user.id, next)
      showToast(next === 'disabled' ? '已停用' : '已启用')
      await reloadUsers()
    } catch (err) {
      showToast(err instanceof Error ? err.message : '状态修改失败')
    }
  }

  const handleUnlock = async (user: DeptUser) => {
    try {
      await unlockUser(user.id)
      showToast('已解锁')
      await reloadUsers()
    } catch (err) {
      showToast(err instanceof Error ? err.message : '解锁失败')
    }
  }

  if (!canList) {
    return (
      <div className="text-[13px] text-[#64748b]">
        你没有 `system:user:list` 权限，看不到用户列表。
      </div>
    )
  }
  if (loading) {
    return <div className="text-[13px] text-[#64748b]">加载中…</div>
  }

  const colCount = 3 + (canSeeDepts ? 1 : 0) + (canSeeRoles ? 1 : 0)
  // 一个写点都没有 = 只读管理员：页面照给，但页尾要说明白
  const readOnly = !canCreate && !canEdit && !canReset && !canStatus && !canUnlock
    && !canAssign && !canAssignRole

  return (
    <div className="flex flex-col gap-4">
      <div className="text-[12px] text-[#64748b] leading-relaxed">
        全部可见用户一页平铺。建号、改部门、换角色在部门配置页也有入口，两处是同一份数据；
        停用会立即吊销那个人的登录会话，解锁是连续错密码锁住后的唯一出口。
      </div>

      <div className="flex items-center gap-3">
        {canCreate && (
          <button
            onClick={() => setCreateOpen(true)}
            className="px-3 py-1.5 rounded-md border border-[#a7f3d0] bg-[#f0fdf4] text-[12px] text-[#047857] cursor-pointer hover:bg-[#a7f3d0]"
          >
            新增用户
          </button>
        )}
        <input
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          spellCheck={false}
          placeholder="搜索用户名或显示名"
          className="w-[260px] px-2.5 py-1.5 border border-[#e2e8f0] rounded-md text-[13px] outline-none focus:border-[#a7f3d0] placeholder:text-[#cbd5e1]"
        />
        <span className="text-[12px] text-[#94a3b8]">{visible.length} 人</span>
      </div>

      <div className="border border-[#e2e8f0] rounded-lg bg-white overflow-hidden">
        <table className="border-collapse w-full">
          <thead>
            <tr className="bg-[#f8fafc]">
              <th className="text-left text-[12px] font-semibold text-[#334155] px-4 py-2 border-b border-[#e2e8f0]">用户</th>
              {canSeeDepts && (
                <th className="text-left text-[12px] font-semibold text-[#334155] px-4 py-2 border-b border-[#e2e8f0] w-[220px]">所属部门</th>
              )}
              {canSeeRoles && (
                <th className="text-left text-[12px] font-semibold text-[#334155] px-4 py-2 border-b border-[#e2e8f0] w-[200px]">角色</th>
              )}
              <th className="text-left text-[12px] font-semibold text-[#334155] px-4 py-2 border-b border-[#e2e8f0] w-[130px]">状态</th>
              <th className="text-left text-[12px] font-semibold text-[#334155] px-4 py-2 border-b border-[#e2e8f0] w-[200px]">操作</th>
            </tr>
          </thead>
          <tbody>
            {visible.map((u) => (
              <tr key={u.id} className="hover:bg-[#f8fafc]">
                <td className="px-4 py-2 border-b border-[#f1f5f9] text-[13px] text-[#334155]">
                  {editingId === u.id ? (
                    <span className="flex items-center gap-2">
                      <input
                        autoFocus
                        value={draft}
                        onChange={(e) => setDraft(e.target.value)}
                        onKeyDown={(e) => {
                          if (e.key === 'Enter') void handleRename(u)
                          else if (e.key === 'Escape') { setEditingId(null); setDraft('') }
                        }}
                        placeholder="显示名"
                        className="flex-1 min-w-0 text-[13px] border border-[#a7f3d0] rounded px-1.5 py-0.5 bg-white text-[#0f172a] outline-none"
                      />
                      <button
                        onClick={() => void handleRename(u)}
                        className="px-2 py-0.5 rounded border border-[#a7f3d0] bg-white text-[11px] text-[#047857] cursor-pointer hover:bg-[#f1f5f9]"
                      >
                        确定
                      </button>
                      <button
                        onClick={() => { setEditingId(null); setDraft('') }}
                        className="px-2 py-0.5 rounded border border-[#e2e8f0] bg-white text-[11px] text-[#64748b] cursor-pointer hover:bg-[#f1f5f9]"
                      >
                        取消
                      </button>
                    </span>
                  ) : (
                    <span className="flex items-center gap-2">
                      <span className="truncate">{u.display_name || u.username}</span>
                      <span className="text-[11px] text-[#94a3b8] font-mono">{u.username}</span>
                      {canEdit && (
                        <button
                          onClick={() => { setEditingId(u.id); setDraft(u.display_name || u.username) }}
                          className="text-[11px] text-[#047857] bg-transparent border-none cursor-pointer p-0 hover:underline"
                        >
                          改名
                        </button>
                      )}
                    </span>
                  )}
                </td>
                {canSeeDepts && (
                  <td className="px-4 py-2 border-b border-[#f1f5f9]">
                    <UserDeptSelect
                      value={u.dept_id}
                      options={options}
                      disabled={!canAssign}
                      onChange={(deptId) => void handleMove(u.id, deptId)}
                    />
                  </td>
                )}
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
                <td className="px-4 py-2 border-b border-[#f1f5f9]">
                  <span className={`text-[11px] px-2 py-0.5 rounded-full ${
                    u.status === 'active'
                      ? 'bg-[rgba(167,243,208,0.5)] text-[#047857]'
                      : 'bg-[#f1f5f9] text-[#94a3b8]'
                  }`}>
                    {u.status === 'active' ? '启用' : '已停用'}
                  </span>
                  {isLocked(u.locked_until) && (
                    <span className="ml-1.5 text-[11px] text-[#b45309]" title={`锁定至 ${u.locked_until}`}>
                      已锁定
                    </span>
                  )}
                </td>
                <td className="px-4 py-2 border-b border-[#f1f5f9]">
                  <span className="flex items-center gap-2">
                    {canReset && (
                      <button
                        onClick={() => setPwdTarget(u)}
                        className="text-[12px] text-[#047857] bg-transparent border-none cursor-pointer p-0 hover:underline"
                      >
                        重置密码
                      </button>
                    )}
                    {canStatus && (
                      <button
                        onClick={() => void handleStatus(u)}
                        className={`text-[12px] bg-transparent border-none cursor-pointer p-0 hover:underline ${
                          u.status === 'active' ? 'text-[#b45309]' : 'text-[#047857]'
                        }`}
                      >
                        {u.status === 'active' ? '停用' : '启用'}
                      </button>
                    )}
                    {/* 没锁着就不出这个按钮 —— 服务端虽然幂等，但出个永远无效的按钮只会让人点 */}
                    {canUnlock && isLocked(u.locked_until) && (
                      <button
                        onClick={() => void handleUnlock(u)}
                        className="text-[12px] text-[#047857] bg-transparent border-none cursor-pointer p-0 hover:underline"
                      >
                        解锁
                      </button>
                    )}
                  </span>
                </td>
              </tr>
            ))}
            {visible.length === 0 && (
              <tr>
                <td colSpan={colCount} className="px-4 py-6 text-center text-[13px] text-[#94a3b8]">
                  {users.length === 0 ? '还没有用户' : '没有匹配的用户'}
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      {/* 只读管理员看得到页面，但一个写按钮都不该出现 */}
      {readOnly && (
        <div className="text-[12px] text-[#94a3b8]">
          你只能查看用户列表，没有增删改的权限点。
        </div>
      )}

      {createOpen && (
        <CreateUserModal
          pick={{ options }}
          roles={canGrantRole ? roles : undefined}
          onClose={() => setCreateOpen(false)}
          onSubmit={handleCreate}
        />
      )}

      {pwdTarget && (
        <ResetPasswordModal
          username={pwdTarget.display_name || pwdTarget.username}
          onClose={() => setPwdTarget(null)}
          onSubmit={(password) => handleReset(pwdTarget.id, password)}
        />
      )}
    </div>
  )
}
