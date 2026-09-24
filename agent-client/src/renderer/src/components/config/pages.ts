/** 配置页标识与入口映射 —— Sidebar 与 AppLayout 共用一份。
 *
 * 以前两边各写一个 `ConfigPage` 联合类型，加页面时只改一边就编译得过、
 * 运行时表现为"点了没反应"。这里收敛成单一来源。
 */
export type ConfigPage =
  | 'skills'
  | 'mcp'
  | 'memory'
  | 'expert'
  | 'rbac'
  | 'dept'
  | 'user'
  | 'model'

/** 侧边栏条目标识。图标表用它当键 —— 联合类型一改，`ENTRY_ICONS` 就编译不过。 */
export type EntryId = 'skills' | 'mcp' | 'memory' | 'expert' | 'automation' | 'system'

/** 页名。单项条目拿它当侧边栏名，多项条目拿它当顶部菜单栏的页签名。 */
export const CONFIG_TITLES: Record<ConfigPage, string> = {
  skills: 'Skills 配置',
  mcp: 'MCP 配置',
  memory: '记忆配置',
  expert: '专家和专家团',
  rbac: '角色管理',
  dept: '部门配置',
  user: '用户管理',
  model: '模型配置'
}

/** 页 → 所需权限点（doc 19-4.2 的 `client:*` 纯前端入口）。
 *
 * 每个 `client:*` 权限点都必须在这里有一个键，且它对应的页必须出现在某个
 * `ENTRY_VIEWS` 条目的 `pages` 里，否则后端下发了权限、前端却没有对应入口，
 * 表现为"给了权限但侧边栏不显示"。
 */
export const PAGE_PERMS: Record<ConfigPage, string> = {
  skills: 'client:skills:config',
  mcp: 'client:mcp:config',
  memory: 'client:memory:config',
  expert: 'client:expert:config',
  rbac: 'client:rbac:config',
  dept: 'client:dept:config',
  user: 'client:user:config',
  model: 'client:model:config'
}

/** 侧边栏条目 → 它下面那组页（doc 19-4.2 的 `client:*` 纯前端入口）。
 *
 * 一个条目对应浮层顶部的**一条菜单栏**：`pages` 多于一个可见页时才出页签。
 */
export const ENTRY_VIEWS: ReadonlyArray<{
  id: EntryId
  /** 侧边栏分组标题。同标题的连着写，先后即渲染先后。 */
  section: string
  /** 该条目下由顶部菜单栏切换的页，第一项是默认页；
   *  空数组 = 占位条目：还没有后端能力，也没有权限点，对所有人可见（点击无反应）。 */
  pages: readonly ConfigPage[]
  /** 侧边栏名与浮层标题；省略时取默认页的页名。 */
  title?: string
}> = [
  { id: 'skills', section: '配置', pages: ['skills'] },
  { id: 'mcp', section: '配置', pages: ['mcp'] },
  { id: 'memory', section: '配置', pages: ['memory'] },
  { id: 'expert', section: '配置', pages: ['expert'] },
  { id: 'automation', section: '配置', pages: [], title: '自动化' },
  // 这几项的顺序就是系统管理下的页签顺序，也是默认落在哪一页（第一项）——
  // 与 §4.2 表的次序一致：用户管理是系统管理下第一条
  { id: 'system', section: '配置', pages: ['user', 'rbac', 'dept', 'model'], title: '系统管理' }
]

export type Entry = (typeof ENTRY_VIEWS)[number]

/** 侧边栏显示名 / 浮层标题。 */
export function entryTitle(entry: Entry): string {
  return entry.title ?? CONFIG_TITLES[entry.pages[0]]
}

/** 页 → 它所属的条目。浮层据此取标题与页签。 */
export function entryOf(page: ConfigPage): Entry | undefined {
  return ENTRY_VIEWS.find((e) => e.pages.includes(page))
}
