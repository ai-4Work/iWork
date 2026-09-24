/** 部门扁平表 → 树行的纯函数。
 *
 *  单独一个模块是为了避开循环 import：`DeptConfig`（左树）与 `UserForm`（建号弹窗的
 *  部门下拉）都要这套展开逻辑，而 `UserForm` 又被 `DeptConfig` import —— 放在任一边
 *  都会绕回来。
 */
import type { DeptNode } from '../../services/api'

export interface DeptRow extends DeptNode {
  depth: number
  /** 自己 + 全部后代的 id */
  subtree: number[]
  /** 从根到这里（不含自己）的 id 链；任一祖先折叠了 => 本行不渲染 */
  ancestors: number[]
}

/** 扁平部门表 → 深度优先的行序列（父行紧跟着它的子孙）。 */
export function buildDeptRows(depts: DeptNode[]): DeptRow[] {
  const childrenOf = new Map<number, DeptNode[]>()
  for (const d of depts) {
    const list = childrenOf.get(d.parent_id) ?? []
    list.push(d)
    childrenOf.set(d.parent_id, list)
  }
  for (const list of childrenOf.values()) {
    list.sort((a, b) => a.order_num - b.order_num || a.id - b.id)
  }

  const rows: DeptRow[] = []
  const walk = (parentId: number, depth: number, ancestors: number[]): number[] => {
    const subtree: number[] = []
    for (const d of childrenOf.get(parentId) ?? []) {
      const row: DeptRow = { ...d, depth, subtree: [], ancestors }
      rows.push(row)
      const kids = walk(d.id, depth + 1, [...ancestors, d.id])
      row.subtree = [d.id, ...kids]
      subtree.push(...row.subtree)
    }
    return subtree
  }
  walk(0, 0, [])
  return rows
}

/** 带缩进的名字 —— 树行与下拉都用它，让层级看得出来。 */
export function deptLabel(row: DeptRow): string {
  return `${'　'.repeat(row.depth)}${row.dept_name}`
}

/** 下拉用的扁平选项，行序即深度优先序。 */
export function deptOptions(depts: DeptNode[]): { id: number; label: string }[] {
  return buildDeptRows(depts).map((row) => ({ id: row.id, label: deptLabel(row) }))
}
