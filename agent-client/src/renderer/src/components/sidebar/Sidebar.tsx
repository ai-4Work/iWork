import { Fragment } from 'react'
import { useTaskStore } from '../../stores/taskStore'
import { useChatStore } from '../../stores/chatStore'
import { activateTask } from '../../stores/persistence'
import { useAuthStore, hasPermi } from '../../stores/authStore'
import { ENTRY_VIEWS, PAGE_PERMS, entryTitle, type ConfigPage, type EntryId } from '../config/pages'
import { Plus } from 'lucide-react'

interface Props {
  onOpenConfig: (page: ConfigPage) => void
  onCloseConfig: () => void
  activeConfig: ConfigPage | null
}

/** 入口图标。与 `ENTRY_VIEWS` 的 id 一一对应 —— 加条目时两处都要动。 */
const ENTRY_ICONS: Record<EntryId, React.ReactNode> = {
  skills: <svg className="w-4 h-4" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><rect x="1.5" y="1.5" width="5" height="5" rx="1" /><rect x="9.5" y="1.5" width="5" height="5" rx="1" /><rect x="1.5" y="9.5" width="5" height="5" rx="1" /><rect x="9.5" y="9.5" width="5" height="5" rx="1" /></svg>,
  mcp: <svg className="w-4 h-4" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><circle cx="5" cy="5" r="2" /><circle cx="11" cy="11" r="2" /><line x1="6.3" y1="6.3" x2="9.7" y2="9.7" /><line x1="5" y1="13" x2="5" y2="7" /><line x1="11" y1="9" x2="11" y2="3" /></svg>,
  memory: <svg className="w-4 h-4" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><circle cx="8" cy="8" r="6" /><path d="M8 6v3M8 11v.01" /></svg>,
  expert: <svg className="w-4 h-4" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><circle cx="6" cy="5" r="2" /><circle cx="11" cy="6" r="2" /><circle cx="4" cy="11" r="2" /><circle cx="10" cy="11" r="2" /></svg>,
  automation:<svg className="w-4 h-4" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><circle cx="8" cy="8" r="2" /><path d="M2 8a6 6 0 0112 0" /><path d="M5 3a6 6 0 015 10" /></svg>,
  system: <svg className="w-4 h-4" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><line x1="2" y1="4.5" x2="14" y2="4.5" /><line x1="2" y1="11.5" x2="14" y2="11.5" /><circle cx="6" cy="4.5" r="2" /><circle cx="10.5" cy="11.5" r="2" /></svg>
}

export function Sidebar({ onOpenConfig, onCloseConfig, activeConfig }: Props) {
  const user = useAuthStore((s) => s.user)
  const logout = useAuthStore((s) => s.logout)
  const tasks = useTaskStore((s) => s.tasks)
  const currentId = useTaskStore((s) => s.currentTaskId)
  const create = useTaskStore((s) => s.create)
  const select = useTaskStore((s) => s.select)
  const del = useTaskStore((s) => s.delete)
  const rename = useTaskStore((s) => s.rename)
  const duplicate = useTaskStore((s) => s.duplicate)
  const processingTaskIds = useChatStore((s) => s.processingTaskIds)
  const permissions = useAuthStore((s) => s.permissions)
  // 分组顺序按表里首次出现定，不假设同 section 必须连成一段
  const sections = [...new Set(ENTRY_VIEWS.map((e) => e.section))]

  return (
    <aside className="w-[252px] min-w-[252px] bg-sidebar-bg text-sidebar-text flex flex-col p-5 gap-0.5 border-r border-sidebar-border h-full">
      {/* Brand */}
      <div className="flex items-center gap-2.5 px-2.5 pb-6 text-lg font-bold text-[#0f172a] tracking-[-0.4px]">
        <svg width="30" height="30" viewBox="0 0 30 30" fill="none" className="flex-shrink-0">
          <rect width="30" height="30" rx="8" fill="#10b981" />
          <path d="M7 15L13 21L23 9" stroke="#fff" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" />
        </svg>
        iWork
      </div>

      {/* New Task */}
      <button
        onClick={() => { onCloseConfig(); create() }}
        className="flex items-center justify-center gap-2.5 py-2.5 mb-1 rounded-md bg-[#0f172a] text-white text-[13px] font-medium hover:bg-[#334155] transition-colors w-full"
      >
        <svg className="w-4 h-4" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
          <path d="M8 3v10M3 8h10" />
        </svg>
        新建任务
      </button>

      {/* Config Nav —— 按 section 分组；一组里一条权限都没有，整组连标题一起不渲染 */}
      {sections.map((title) => {
        const entries = ENTRY_VIEWS.filter(
          (e) => e.section === title
            && (e.pages.length === 0 || hasPermi(permissions, e.pages.map((p) => PAGE_PERMS[p])))
        )
        if (entries.length === 0) return null
        return (
          <Fragment key={title}>
            <div className="text-[10px] font-semibold uppercase tracking-[0.6px] text-sidebar-text-dim pt-4 pb-1 px-2.5">
              {title}
            </div>
            {entries.map((entry) => (
              <SidebarBtn
                key={entry.id}
                icon={ENTRY_ICONS[entry.id]}
                label={entryTitle(entry)}
                active={activeConfig !== null && entry.pages.includes(activeConfig)}
                onClick={() => {
                  // 取第一个**有权限**的页：只有部门权限的人，不该点开就落在角色管理上
                  const first = entry.pages.find((p) => hasPermi(permissions, PAGE_PERMS[p]))
                  if (first) onOpenConfig(first)
                }}
              />
            ))}
          </Fragment>
        )
      })}

      {/* Task List */}
      <div className="text-[10px] font-semibold uppercase tracking-[0.6px] text-sidebar-text-dim pt-4 pb-1 px-2.5">
        工作任务
      </div>
      <div className="flex flex-col gap-px overflow-y-auto flex-1 min-h-0">
        {tasks.map((t) => (
          <div
            key={t.id}
            onClick={() => { onCloseConfig(); select(t.id); void activateTask(t.id) }}
            onContextMenu={(e) => {
              e.preventDefault()
              const menu = document.getElementById('ctxMenu')
              if (!menu) return
              menu.innerHTML = [
                { label: '重命名', action: () => rename(t.id, prompt('重命名任务:', t.title) || t.title) },
                { label: '复制任务', action: () => duplicate(t.id) },
                { label: '删除任务', cls: 'text-red-500', action: () => del(t.id) }
              ].map(item =>
                `<div class="ctx-menu-item${item.cls ? ' ' + item.cls : ''}" data-action="${item.label}">${item.label}</div>`
              ).join('')
              menu.style.left = e.clientX + 'px'
              menu.style.top = e.clientY + 'px'
              menu.classList.add('show')
              menu.querySelectorAll('.ctx-menu-item').forEach((el, i) => {
                el.addEventListener('click', () => {
                  menu.classList.remove('show')
                    ;[
                      () => rename(t.id, prompt('重命名任务:', t.title) || t.title),
                      () => duplicate(t.id),
                      () => del(t.id)
                    ][i]()
                })
              })
              const closeCtx = (ev: MouseEvent) => {
                if (!(ev.target as HTMLElement).closest('.ctx-menu')) {
                  menu.classList.remove('show')
                  document.removeEventListener('click', closeCtx)
                }
              }
              setTimeout(() => document.addEventListener('click', closeCtx), 10)
            }}
            className={`task-item py-2 px-2.5 rounded-md text-[13px] cursor-pointer flex items-center gap-2 transition-colors ${t.id === currentId
              ? 'bg-[rgba(167,243,208,0.4)] text-[#047857] font-medium'
              : 'text-sidebar-text hover:bg-sidebar-hover'
              }`}
          >
            <span className="flex-1 overflow-hidden text-ellipsis whitespace-nowrap">{t.title}</span>
            {t.agentName && (
              <span className={`text-[9px] font-medium px-1 py-px rounded flex-shrink-0 ml-0.5 max-w-[72px] overflow-hidden text-ellipsis whitespace-nowrap ${
                t.agentType === 'team' ? 'bg-[#fef3c7] text-[#b45309]' : 'bg-[#eef2ff] text-[#4f46e5]'
              }`}>
                {t.agentName}
              </span>
            )}
            {processingTaskIds[t.id] && (
              <span
                className="w-1.5 h-1.5 rounded-full bg-[#f59e0b] animate-pulse flex-shrink-0"
                title="处理中"
              />
            )}
            <span className="text-[11px] text-sidebar-text-dim flex-shrink-0">{t.time}</span>
          </div>
        ))}
      </div>

      {/* User Footer */}
      <div className="border-t border-sidebar-divider pt-3 mt-1 flex items-center gap-2.5 text-xs text-sidebar-text-dim">
        <div className="w-[26px] h-[26px] rounded-full bg-[#a7f3d0] flex items-center justify-center flex-shrink-0 text-[#047857] text-xs font-semibold">
          <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><circle cx="8" cy="5" r="3" /><path d="M2 14c0-3.3 2.7-6 6-6s6 2.7 6 6" /></svg>
        </div>
        <span className="flex-1 truncate" title={user?.username}>{user?.display_name || user?.username || ''}</span>
        <button
          onClick={logout}
          title="退出登录"
          className="flex items-center gap-1 flex-shrink-0 px-2 py-1 rounded-md border-none bg-none font-sans text-xs text-sidebar-text-dim cursor-pointer hover:bg-sidebar-hover hover:text-[#0f172a]"
        >
          <svg width="13" height="13" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round"><path d="M6 14H3.5A1.5 1.5 0 0 1 2 12.5v-9A1.5 1.5 0 0 1 3.5 2H6" /><path d="M10.5 11 14 8l-3.5-3" /><path d="M14 8H6" /></svg>
          退出
        </button>
      </div>
    </aside>
  )
}

function SidebarBtn({ icon, label, active, onClick }: { icon: React.ReactNode; label: string; active?: boolean; onClick: () => void }) {
  return (
    <button
      onClick={onClick}
      className={`flex items-center gap-2.5 py-[9px] px-2.5 rounded-md cursor-pointer text-[13px] transition-colors w-full text-left border-none bg-none font-sans tracking-[-0.1px] ${active
        ? 'bg-[rgba(167,243,208,0.4)] text-[#047857] font-medium'
        : 'text-sidebar-text hover:bg-sidebar-hover hover:text-[#0f172a]'
        }`}
    >
      {icon}
      {label}
    </button>
  )
}
