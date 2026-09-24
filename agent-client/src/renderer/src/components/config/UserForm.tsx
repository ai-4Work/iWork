/** 用户管理页与部门配置页**共用**的控件。
 *
 *  两页都能建号、改部门、换角色（doc 19-4.2 表 34–36 行，同一批权限点、同一支 API）。
 *  共用一份组件是「两处不漂移」的第三条：控件判定与 `disabled` 条件只有一处可改，
 *  不会出现"部门页给得出、用户页给不出"这种各走各的可见性。
 */
import { useState } from 'react'
import type { RbacRole } from '../../services/api'

/** 建号弹窗的部门来源：部门页是「弹窗开在哪就落在哪」（只读显示），
 *  用户管理页要自己在下拉里挑。两种形状用判别联合表达，调用方传不混。 */
export type DeptPick =
  | { lockedId: number; lockedName: string }
  | { options: { id: number; label: string }[] }

/** `locked_until` 是否还在未来。
 *
 *  自动锁定是到期自解的（doc 18-3.3），所以「解锁」那个按钮能不能出，
 *  得看这个时间戳过没过 —— 服务端不会帮你清，只等你来清。
 *  纯函数，方便在渲染里直接调。 */
export function isLocked(lockedUntil: string | null): boolean {
  if (!lockedUntil) return false
  return new Date(lockedUntil).getTime() > Date.now()
}

const SELECT_CLASS =
  'w-full text-[12px] border border-[#e2e8f0] rounded px-1.5 py-1 bg-white text-[#334155] cursor-pointer disabled:opacity-50 disabled:cursor-not-allowed'

/** 挪部门的下拉。`options` 由 `deptOptions(depts)` 给。 */
export function UserDeptSelect({ value, options, disabled, onChange }: {
  value: number | null
  options: { id: number; label: string }[]
  disabled: boolean
  onChange: (deptId: number) => void
}) {
  return (
    <select
      value={value ?? ''}
      disabled={disabled}
      onChange={(e) => onChange(Number(e.target.value))}
      className={SELECT_CLASS}
    >
      <option value="" disabled>未选择</option>
      {options.map((o) => (
        <option key={o.id} value={o.id}>{o.label}</option>
      ))}
    </select>
  )
}

/** 换角色的下拉。服务端是整集替换，单选就是把新值包成单元素数组递过去。
 *
 *  `emptyLabel` 决定空值项是「展示」还是「选择」：表格里它是"这个人没角色"的状态，
 *  选中就是递一个空角色过去，所以禁掉；建号弹窗里它是"不指定，用默认角色"这个正当
 *  选择，于是可点。两种用法共用这一个组件，这条判定只有一处。 */
export function UserRoleSelect({ value, roles, disabled, emptyLabel, onChange }: {
  value: number | null
  roles: RbacRole[]
  disabled: boolean
  /** 给了它，空值项就可选，且用它当文案 */
  emptyLabel?: string
  onChange: (roleId: number | null) => void
}) {
  return (
    <select
      value={value ?? ''}
      disabled={disabled}
      onChange={(e) => onChange(e.target.value === '' ? null : Number(e.target.value))}
      className={SELECT_CLASS}
    >
      <option value="" disabled={emptyLabel === undefined}>{emptyLabel ?? '未选择'}</option>
      {roles.map((r) => (
        <option key={r.id} value={r.id}>{r.role_name}</option>
      ))}
    </select>
  )
}

/** 管理员建号弹窗。密码要当面转交给本人，所以带「显示」开关 —— 掩码下没法核对。
 *
 *  `roles` 在时多出「角色」一项：一次就把人和角色定好，省掉"先建号、再去列表里改角色"
 *  那一步。调用方没给 `roles`（缺 `system:role:assign` 或 `system:role:list`）就没有这一项，
 *  建出来的号是默认角色。 */
export function CreateUserModal({ pick, roles, onClose, onSubmit }: {
  pick: DeptPick
  /** 可选角色。`undefined` = 不出这一项 */
  roles?: RbacRole[]
  onClose: () => void
  /** 返回 false 时弹窗不关（用户名被占、密码不合规、角色不可授都能改了再交） */
  onSubmit: (
    username: string, password: string, deptId: number, roleId: number | null,
  ) => Promise<boolean>
}) {
  const [deptId, setDeptId] = useState<number | null>(
    'lockedId' in pick ? pick.lockedId : (pick.options[0]?.id ?? null)
  )
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  /** `null` = 不指定，由服务端挂默认角色 —— 客户端不认识"哪个角色是默认" */
  const [roleId, setRoleId] = useState<number | null>(null)
  const [showPwd, setShowPwd] = useState(false)
  const [submitting, setSubmitting] = useState(false)

  const isValid = username.trim().length > 0 && password.length >= 8 && deptId !== null

  const handleSubmit = async () => {
    if (!isValid || submitting || deptId === null) return
    setSubmitting(true)
    try {
      if (await onSubmit(username.trim(), password, deptId, roleId)) onClose()
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/20" onClick={onClose}>
      <div
        className="bg-white rounded-xl shadow-lg p-6 w-[420px]"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between">
          <h3 className="text-[15px] font-semibold text-[#0f172a]">添加用户</h3>
          <button onClick={onClose} className="text-[#94a3b8] hover:text-[#0f172a] cursor-pointer">
            <svg className="w-4 h-4" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="2"><path d="M4 4l8 8M12 4l-8 8"/></svg>
          </button>
        </div>
        <p className="mt-1 mb-4 text-[12px] text-[#64748b]">
          {'lockedId' in pick
            ? `新账号会落在「${pick.lockedName}」。`
            : '选一个落地部门。'}
          {roles
            ? '角色不选就是默认角色，之后可在列表里改。'
            : '角色为普通用户，之后可在列表里改。'}
        </p>

        <div className="space-y-3.5">
          <div>
            <label className="block text-[12px] font-medium text-[#64748b] mb-1">用户名 *</label>
            <input
              autoFocus
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              spellCheck={false}
              placeholder="3–32 位小写字母、数字、_ 或 -"
              className="w-full px-2.5 py-[7px] border border-[#e2e8f0] rounded-md text-[13px] outline-none focus:border-[#a7f3d0] placeholder:text-[#cbd5e1]"
            />
          </div>
          <div>
            <label className="block text-[12px] font-medium text-[#64748b] mb-1">密码 *</label>
            <div className="flex items-center gap-2">
              <input
                type={showPwd ? 'text' : 'password'}
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                autoComplete="new-password"
                placeholder="至少 8 位"
                className="flex-1 px-2.5 py-[7px] border border-[#e2e8f0] rounded-md text-[13px] outline-none focus:border-[#a7f3d0] placeholder:text-[#cbd5e1]"
              />
              <label className="flex items-center gap-1 text-[12px] text-[#64748b] cursor-pointer select-none whitespace-nowrap">
                <input
                  type="checkbox"
                  checked={showPwd}
                  onChange={(e) => setShowPwd(e.target.checked)}
                />
                显示
              </label>
            </div>
          </div>
          {'options' in pick && (
            <div>
              <label className="block text-[12px] font-medium text-[#64748b] mb-1">所属部门 *</label>
              <UserDeptSelect
                value={deptId}
                options={pick.options}
                disabled={false}
                onChange={setDeptId}
              />
            </div>
          )}
          {roles && (
            <div>
              <label className="block text-[12px] font-medium text-[#64748b] mb-1">角色</label>
              <UserRoleSelect
                value={roleId}
                roles={roles}
                disabled={false}
                emptyLabel="不指定（默认角色）"
                onChange={setRoleId}
              />
            </div>
          )}
        </div>

        <div className="flex justify-end gap-2 mt-5">
          <button
            onClick={onClose}
            className="px-4 py-1.5 rounded-md text-[13px] border border-[#e2e8f0] text-[#64748b] cursor-pointer hover:bg-[#f8fafc]"
          >
            取消
          </button>
          <button
            onClick={() => void handleSubmit()}
            disabled={!isValid || submitting}
            className={`px-4 py-1.5 rounded-md text-[13px] font-medium cursor-pointer border ${
              isValid && !submitting
                ? 'border-[#a7f3d0] text-[#047857] bg-[#f0fdf4] hover:bg-[#a7f3d0]'
                : 'border-[#e2e8f0] text-[#cbd5e1] bg-[#f8fafc] cursor-not-allowed'
            }`}
          >
            {submitting ? '创建中…' : '确定'}
          </button>
        </div>
      </div>
    </div>
  )
}

/** 管理员重置密码弹窗（doc 18-3.5）。带「显示」开关，理由同建号 —— 密码要当面转交本人。 */
export function ResetPasswordModal({ username, onClose, onSubmit }: {
  username: string
  onClose: () => void
  onSubmit: (password: string) => Promise<boolean>
}) {
  const [password, setPassword] = useState('')
  const [showPwd, setShowPwd] = useState(false)
  const [submitting, setSubmitting] = useState(false)

  const isValid = password.length >= 8

  const handleSubmit = async () => {
    if (!isValid || submitting) return
    setSubmitting(true)
    try {
      if (await onSubmit(password)) onClose()
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/20" onClick={onClose}>
      <div
        className="bg-white rounded-xl shadow-lg p-6 w-[420px]"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between">
          <h3 className="text-[15px] font-semibold text-[#0f172a]">重置密码</h3>
          <button onClick={onClose} className="text-[#94a3b8] hover:text-[#0f172a] cursor-pointer">
            <svg className="w-4 h-4" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="2"><path d="M4 4l8 8M12 4l-8 8"/></svg>
          </button>
        </div>
        <p className="mt-1 mb-4 text-[12px] text-[#64748b]">
          为「{username}」设新密码。提交后他手上所有登录会话立即失效，需要用新密码重新登录。
        </p>

        <div>
          <label className="block text-[12px] font-medium text-[#64748b] mb-1">新密码 *</label>
          <div className="flex items-center gap-2">
            <input
              autoFocus
              type={showPwd ? 'text' : 'password'}
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              autoComplete="new-password"
              placeholder="至少 8 位"
              className="flex-1 px-2.5 py-[7px] border border-[#e2e8f0] rounded-md text-[13px] outline-none focus:border-[#a7f3d0] placeholder:text-[#cbd5e1]"
            />
            <label className="flex items-center gap-1 text-[12px] text-[#64748b] cursor-pointer select-none whitespace-nowrap">
              <input
                type="checkbox"
                checked={showPwd}
                onChange={(e) => setShowPwd(e.target.checked)}
              />
              显示
            </label>
          </div>
        </div>

        <div className="flex justify-end gap-2 mt-5">
          <button
            onClick={onClose}
            className="px-4 py-1.5 rounded-md text-[13px] border border-[#e2e8f0] text-[#64748b] cursor-pointer hover:bg-[#f8fafc]"
          >
            取消
          </button>
          <button
            onClick={() => void handleSubmit()}
            disabled={!isValid || submitting}
            className={`px-4 py-1.5 rounded-md text-[13px] font-medium cursor-pointer border ${
              isValid && !submitting
                ? 'border-[#a7f3d0] text-[#047857] bg-[#f0fdf4] hover:bg-[#a7f3d0]'
                : 'border-[#e2e8f0] text-[#cbd5e1] bg-[#f8fafc] cursor-not-allowed'
            }`}
          >
            {submitting ? '保存中…' : '确定'}
          </button>
        </div>
      </div>
    </div>
  )
}
