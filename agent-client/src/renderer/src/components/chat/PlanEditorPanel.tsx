import { useState, useEffect, useRef } from 'react'
import { useChatStore } from '../../stores/chatStore'
import { useTaskStore } from '../../stores/taskStore'

export function PlanEditorPanel() {
  const currentEditingPlanMsgIdx = useChatStore((s) => s.currentEditingPlanMsgIdx)
  const savePlanFromEditor = useChatStore((s) => s.savePlanFromEditor)
  const closePlanEditor = useChatStore((s) => s.closePlanEditor)

  const [text, setText] = useState('')
  const textareaRef = useRef<HTMLTextAreaElement>(null)
  const isOpen = currentEditingPlanMsgIdx != null

  // Populate editor text from the message being edited
  useEffect(() => {
    if (currentEditingPlanMsgIdx != null) {
      const task = useTaskStore.getState().getCurrentTask()
      const msg = task?.messages[currentEditingPlanMsgIdx]
      if (msg) {
        // Collect text from segments before the first 'generated' plan event
        const segs = msg.segments || []
        const generatedIdx = segs.findIndex(s => s.type === 'generated')
        const textSegs = segs.slice(0, generatedIdx >= 0 ? generatedIdx : undefined)
        const planText = textSegs
          .filter((s): s is { type: 'text'; content: string } => s.type === 'text')
          .map(s => s.content)
          .join('')
        setText(planText || msg.content || '')
      }
    } else {
      setText('')
    }
  }, [currentEditingPlanMsgIdx])

  // Focus textarea after open animation
  useEffect(() => {
    if (isOpen) {
      setTimeout(() => {
        if (textareaRef.current) {
          textareaRef.current.focus()
        }
      }, 350)
    }
  }, [isOpen])

  if (!isOpen) return null

  return (
    <div className="w-[420px] min-w-[320px] bg-white ml-0 mr-2 my-2 rounded-[14px] border border-[#e2e8f0] shadow-lg flex flex-col transition-all">
      <div className="flex items-center gap-2.5 px-5 py-4 border-b border-[#e2e8f0] flex-shrink-0">
        <h3 className="flex-1 text-[15px] font-semibold text-[#0f172a] tracking-[-0.2px]">编辑计划</h3>
        <button
          onClick={closePlanEditor}
          className="w-7 h-7 rounded-md border border-[#e2e8f0] bg-white cursor-pointer text-sm flex items-center justify-center text-[#64748b] hover:bg-[#f1f5f9] hover:text-[#0f172a] transition-colors"
          title="关闭"
        >
          ×
        </button>
      </div>

      <div className="flex-1 flex flex-col gap-3 p-4 overflow-y-auto">
        <textarea
          ref={textareaRef}
          className="flex-1 min-h-[200px] w-full px-3.5 py-3 border border-[#e2e8f0] rounded-md text-[13px] text-[#0f172a] resize-none outline-none leading-relaxed focus:border-[#a7f3d0] focus:shadow-[0_0_0_3px_rgba(167,243,208,0.15)] transition-colors"
          placeholder="在此编辑执行计划..."
          value={text}
          onChange={(e) => setText(e.target.value)}
        />
      </div>

      <div className="flex justify-end gap-2 px-5 py-3 border-t border-[#e2e8f0] flex-shrink-0">
        <button
          onClick={closePlanEditor}
          className="px-5 py-2 rounded-md text-[13px] font-medium bg-white text-[#64748b] border border-[#e2e8f0] hover:bg-[#f1f5f9] hover:text-[#0f172a] transition-colors cursor-pointer"
        >
          取消
        </button>
        <button
          onClick={() => {
            if (text.trim()) {
              savePlanFromEditor(text.trim())
            }
          }}
          disabled={!text.trim()}
          className="px-5 py-2 rounded-md text-[13px] font-medium bg-[#0f172a] text-white border border-[#0f172a] hover:bg-[#334155] transition-colors cursor-pointer disabled:opacity-40"
        >
          保存修改
        </button>
      </div>
    </div>
  )
}
