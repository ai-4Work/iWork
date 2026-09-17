import { create } from 'zustand'
import type { Command } from '../types'

const BUILTIN_COMMANDS: Command[] = [
  { id: 'explain', trigger: '/explain', label: '解释代码', description: '解释选中的代码' },
  { id: 'fix', trigger: '/fix', label: '修复问题', description: '修复代码中的问题' },
  { id: 'test', trigger: '/test', label: '生成测试', description: '为选中的代码生成测试' },
  { id: 'refactor', trigger: '/refactor', label: '重构代码', description: '重构选中的代码' }
]

interface CommandState {
  commands: Command[]
  filter: (search: string) => Command[]
}

export const useCommandStore = create<CommandState>(() => ({
  commands: BUILTIN_COMMANDS,

  filter: (search: string) => {
    const q = search.toLowerCase().replace(/^\//, '')
    if (!q) return BUILTIN_COMMANDS
    return BUILTIN_COMMANDS.filter(
      (c) =>
        c.trigger.toLowerCase().includes(q) ||
        c.label.toLowerCase().includes(q) ||
        c.description.toLowerCase().includes(q)
    )
  }
}))
