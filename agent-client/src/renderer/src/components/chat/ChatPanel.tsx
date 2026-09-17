import { useCallback, useRef } from 'react'
import { useTaskStore } from '../../stores/taskStore'
import { useChatStore } from '../../stores/chatStore'
import { MessageList } from './MessageList'
import { ChatInput } from './ChatInput'

export function ChatPanel() {
  const task = useTaskStore((s) => s.getCurrentTask())
  const currentTaskId = useTaskStore((s) => s.currentTaskId)
  // 处理态按任务：只反映"当前任务"是否在跑，其它任务在后台跑不影响本面板。
  const isProcessing = useChatStore((s) => (currentTaskId ? !!s.processingTaskIds[currentTaskId] : false))
  const hasMessages = task && task.messages && task.messages.length > 0
  const scrollContainerRef = useRef<HTMLDivElement>(null)

  return (
    <div className={`flex-1 flex flex-col min-h-0 overflow-hidden ${hasMessages ? '' : 'justify-center'}`}>
      {hasMessages && (
        <div className="flex-shrink-0 flex items-center px-5 py-3 gap-2 border-b border-[#e2e8f0] bg-white rounded-t-[14px]">
          <span className="text-[13px] font-medium text-[#0f172a] truncate flex-1">
            {task?.title || 'iWork'}
          </span>
          {isProcessing && (
            <span className="text-[11px] text-[#f59e0b] font-medium flex items-center gap-1">
              <span className="inline-block w-2 h-2 rounded-full bg-[#f59e0b] animate-pulse" />
              处理中...
            </span>
          )}
        </div>
      )}

      {/* Full-width scroll container — scrollbar on the right edge */}
      <div
        ref={scrollContainerRef}
        className={`flex-1 min-h-0 overflow-y-auto overflow-x-hidden ${hasMessages ? '' : 'flex flex-col justify-center'}`}
      >
        <div className={`max-w-[768px] w-full mx-auto px-6 py-4 ${hasMessages ? 'pb-[30vh]' : ''}`}>
          <MessageList scrollContainerRef={scrollContainerRef} />
        </div>
      </div>

      <ChatInput />
    </div>
  )
}
