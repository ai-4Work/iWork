/** 配置页标识与入口映射 —— Sidebar 与 AppLayout 共用一份。
 *
 * 以前两边各写一个 `ConfigPage` 联合类型，加页面时只改一边就编译得过、
 * 运行时表现为"点了没反应"。这里收敛成单一来源。
 */
export type ConfigPage = 'skills' | 'mcp' | 'memory' | 'expert' | 'rbac' | 'dept'

export const CONFIG_TITLES: Record<ConfigPage, string> = {
  skills: 'Skills 配置',
  mcp: 'MCP 配置',
  memory: '记忆配置',
  expert: '专家和专家团',
  rbac: '角色管理',
  dept: '部门配置'
}

/** 侧边栏入口 → 所需权限点（doc 19-4.2 的 `client:*` 纯前端入口）。
 *
 * 每个 `client:*` 权限点都必须在这里有一个键，否则后端下发了权限、
 * 前端却没有对应入口，表现为"给了权限但侧边栏不显示"。
 */
export const ENTRY_VIEWS: ReadonlyArray<{
  page: ConfigPage
  perms: string
  /** 侧边栏分组标题。同标题的连着写，先后即渲染先后。 */
  section: string
}> = [
  { page: 'skills', perms: 'client:skills:config', section: '配置' },
  { page: 'mcp', perms: 'client:mcp:config', section: '配置' },
  { page: 'memory', perms: 'client:memory:config', section: '配置' },
  { page: 'expert', perms: 'client:expert:config', section: '配置' },
  { page: 'rbac', perms: 'client:rbac:config', section: '系统管理' },
  { page: 'dept', perms: 'client:dept:config', section: '系统管理' }
]
