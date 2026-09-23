import { useState, useEffect } from 'react'
import { Sidebar } from '../sidebar/Sidebar'
import { ChatPanel } from '../chat/ChatPanel'
import { PlanEditorPanel } from '../chat/PlanEditorPanel'
import { MultiAgentPanel } from '../chat/MultiAgentPanel'
import { SkillsConfig } from '../config/SkillsConfig'
import { McpConfig } from '../config/McpConfig'
import { MemoryConfig } from '../config/MemoryConfig'
import { ExpertConfig } from '../config/ExpertConfig'
import { RbacConfig } from '../config/RbacConfig'
import { DeptConfig } from '../config/DeptConfig'
import { CONFIG_TITLES, type ConfigPage } from '../config/pages'
import { useChatStore } from '../../stores/chatStore'
import { useTaskStore } from '../../stores/taskStore'
import { useMultiAgentStore } from '../../stores/multiAgentStore'

export function AppLayout() {
  const [configPage, setConfigPage] = useState<ConfigPage | null>(null)
  const currentEditingPlanMsgIdx = useChatStore((s) => s.currentEditingPlanMsgIdx)
  const isPlanEditorOpen = currentEditingPlanMsgIdx != null
  const currentTask = useTaskStore((s) => s.getCurrentTask())
  const multiAgentSession = useMultiAgentStore((s) => s.session)
  const showMultiAgent = currentTask?.agentType === 'team' && multiAgentSession !== null

  // Close dropdowns on outside click
  useEffect(() => {
    const handler = (e: MouseEvent) => {
      // Close context menu
      const menu = document.getElementById('ctxMenu')
      if (menu && !(e.target as HTMLElement).closest('.ctx-menu') && !(e.target as HTMLElement).closest('.task-item')) {
        menu.classList.remove('show')
      }
    }
    document.addEventListener('click', handler)
    return () => document.removeEventListener('click', handler)
  }, [])

  return (
    <div className="flex h-screen w-screen overflow-hidden bg-[#e2e8f0]">
      <Sidebar
        onOpenConfig={(page) => setConfigPage(configPage === page ? null : page)}
        onCloseConfig={() => setConfigPage(null)}
        activeConfig={configPage}
      />

      {/* Main chat area */}
      <main
        className={`flex-1 flex flex-col bg-[#f8fafc] shadow-lg relative overflow-hidden border border-[#e2e8f0] transition-all ${
          isPlanEditorOpen ? 'mr-1' : ''
        }`}
      >
        {showMultiAgent ? <MultiAgentPanel /> : <ChatPanel />}

        {/* Config panel overlay */}
        {configPage && (
          <div className="absolute top-0 left-0 right-0 bottom-0 bg-[#f8fafc] z-20 flex flex-col rounded-[14px] m-0 border border-[#e2e8f0]">
            <div className="flex items-center gap-3.5 px-6 py-[18px] border-b border-[#e2e8f0] bg-white rounded-t-[14px]">
              <button
                onClick={() => setConfigPage(null)}
                className="w-8 h-8 rounded-md border border-[#e2e8f0] bg-white cursor-pointer text-[15px] flex items-center justify-center text-[#64748b] hover:bg-[#f1f5f9] hover:text-[#0f172a] transition-colors"
              >
                <svg className="w-4 h-4" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round"><path d="M10 3L5 8l5 5"/></svg>
              </button>
              <h3 className="text-[17px] font-semibold text-[#0f172a] tracking-[-0.2px]">{CONFIG_TITLES[configPage]}</h3>
            </div>
            <div className="flex-1 overflow-y-auto p-6">
              {configPage === 'skills' && <SkillsConfig />}
              {configPage === 'mcp' && <McpConfig />}
              {configPage === 'memory' && <MemoryConfig />}
              {configPage === 'expert' && <ExpertConfig onClose={() => setConfigPage(null)} />}
              {configPage === 'rbac' && <RbacConfig />}
              {configPage === 'dept' && <DeptConfig />}
            </div>
          </div>
        )}
      </main>

      {/* Plan Editor Panel */}
      <PlanEditorPanel />

      {/* Toast */}
      <div id="toast" className="fixed top-5 left-1/2 -translate-x-1/2 bg-[#0f172a] text-white py-2.5 px-5 rounded-md text-[13px] z-[999] opacity-0 transition-opacity pointer-events-none font-medium" />

      {/* Context Menu */}
      <div id="ctxMenu" className="ctx-menu fixed bg-white border border-[#e2e8f0] rounded-[10px] shadow-lg min-w-[160px] p-1 z-[200] hidden" />
    </div>
  )
}
