import type { ReactNode } from 'react'
import { usePermi } from '../stores/authStore'

/** 按权限点决定渲不渲染子节点。`perms` 传数组表示**任一满足**（与后端同义）。
 *
 * 只做展示层收起，**不是安全边界** —— 真正的闸门在后端 `require_permission`。
 * 前端藏起来的按钮，直接调接口照样会被 403 挡下（doc 19-6.5）。
 */
export function Permi({ perms, children }: { perms: string | string[]; children: ReactNode }) {
  return usePermi(perms) ? <>{children}</> : null
}
