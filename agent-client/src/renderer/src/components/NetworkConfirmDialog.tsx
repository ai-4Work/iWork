import { useEffect } from 'react'
import { ipcClient } from '../services/ipcClient'
import { useNetworkApprovalStore } from '../stores/networkApprovalStore'

// 无超时：弹窗一直等用户明确点「同意 / 拒绝」，不自动拒绝
export function NetworkConfirmDialog() {
  const pending = useNetworkApprovalStore((s) => s.pending)
  const setPending = useNetworkApprovalStore((s) => s.setPending)

  const resolve = (approved: boolean) => {
    if (!pending) return
    ipcClient.proxy.resolveNetwork(pending.host, pending.protocol, approved).catch(() => {})
    setPending(null)
  }

  useEffect(() => {
    ipcClient.proxy.onNetworkAsk(({ host, protocol }) => {
      setPending({ host, protocol })
    })
  }, [setPending])

  if (!pending) return null

  return (
    <div className="fixed inset-0 z-[9999] flex items-center justify-center bg-black/40">
      <div className="bg-[#1e1e2e] rounded-xl shadow-2xl p-6 w-[420px] text-[#cdd6f4]">
        <div className="text-[15px] font-semibold mb-1">网络访问需要确认</div>
        <div className="text-[13px] text-[#a6adc8] mb-4">
          Agent 想访问 <span className="text-[#f5e0dc] font-medium">{pending.host}</span>（{pending.protocol}）
        </div>
        <div className="flex justify-end gap-2">
          <button
            onClick={() => resolve(false)}
            className="px-4 py-1.5 rounded-md text-[13px] bg-transparent border border-[#45475a] text-[#a6adc8] hover:bg-[#313244] cursor-pointer"
          >
            拒绝
          </button>
          <button
            onClick={() => resolve(true)}
            className="px-4 py-1.5 rounded-md text-[13px] bg-[#89b4fa] text-[#1e1e2e] font-medium hover:bg-[#9dc4fb] cursor-pointer"
          >
            同意
          </button>
        </div>
      </div>
    </div>
  )
}
