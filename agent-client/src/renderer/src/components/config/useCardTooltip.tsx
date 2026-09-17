import { useState, type MouseEvent, type ReactNode } from 'react'

type Tip = { rect: DOMRect; name: string; desc: string }

/**
 * 卡片文字被截断时，悬停弹出浮层显示完整内容。
 *
 * 用法：卡片容器挂 `{...hoverProps(name, desc)}`，标题/描述分别加
 * `data-tip-name` / `data-tip-desc` 标记，浮层节点渲染 `tooltip`。
 *
 * 浮层用 fixed 定位而非 absolute：配置面板内容区是 overflow-y-auto，
 * 按 CSS 规范两轴都会裁剪，挂在卡片内的绝对定位浮层会被容器顶边切掉。
 */
export function useCardTooltip() {
  const [tip, setTip] = useState<Tip | null>(null)

  // 仅当标题或描述真的被截断时才弹浮层，短文本不打扰
  const hoverProps = (name: string, desc: string) => ({
    onMouseEnter: (e: MouseEvent<HTMLDivElement>) => {
      const card = e.currentTarget
      const nameEl = card.querySelector('[data-tip-name]') as HTMLElement | null
      const descEl = card.querySelector('[data-tip-desc]') as HTMLElement | null
      const nameCut = nameEl ? nameEl.scrollWidth > nameEl.clientWidth : false
      const descCut = descEl ? descEl.scrollHeight > descEl.clientHeight : false
      if (!nameCut && !descCut) return
      setTip({ rect: card.getBoundingClientRect(), name, desc })
    },
    onMouseLeave: () => setTip(null)
  })

  const tooltip: ReactNode = tip ? (
    <div
      className="fixed z-[300] pointer-events-none w-max max-w-[300px] px-3 py-2 rounded-md bg-[#0f172a] text-white shadow-lg"
      style={{
        left: tip.rect.left + tip.rect.width / 2,
        top: tip.rect.top < 90 ? tip.rect.bottom + 8 : tip.rect.top - 8,
        transform: tip.rect.top < 90 ? 'translateX(-50%)' : 'translate(-50%, -100%)'
      }}
    >
      <div className="text-[11px] font-semibold break-words">{tip.name}</div>
      {tip.desc && <div className="text-[11px] text-[#cbd5e1] mt-0.5 whitespace-pre-wrap break-words">{tip.desc}</div>}
    </div>
  ) : null

  return { hoverProps, tooltip }
}
