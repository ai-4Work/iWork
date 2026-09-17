import { useRef, useEffect } from 'react'

// Auto-scrolls a scroll container to the bottom as its content grows, matching the
// single-chat MessageList behavior: it stays pinned to the bottom while streaming,
// but lets the user scroll up without being yanked back down.
//
// Uses scrollTop/scrollTo on the container itself (NOT scrollIntoView) so it only
// scrolls vertically and never drags an ancestor (e.g. the horizontal columns
// scroller) to the right.

export function useAutoScroll(messageCount: number, segmentCount: number, isStreaming: boolean) {
  const scrollRef = useRef<HTMLDivElement>(null)
  const contentRef = useRef<HTMLDivElement>(null)
  const isAtBottomRef = useRef(true)
  const prevCountRef = useRef(messageCount)
  const prevSegCountRef = useRef(segmentCount)

  const scrollToBottom = (behavior: 'auto' | 'smooth' = 'auto') => {
    const el = scrollRef.current
    if (!el) return
    if (behavior === 'smooth') {
      el.scrollTo({ top: el.scrollHeight, behavior: 'smooth' })
    } else {
      el.scrollTop = el.scrollHeight
    }
  }

  // Track whether the user is at the bottom of the scroll container
  useEffect(() => {
    const el = scrollRef.current
    if (!el) return
    const handler = () => {
      const threshold = 80
      isAtBottomRef.current = el.scrollHeight - el.scrollTop - el.clientHeight < threshold
    }
    el.addEventListener('scroll', handler, { passive: true })
    return () => el.removeEventListener('scroll', handler)
  }, [])

  // Auto-scroll when content height changes (text/thinking/tools/segments growth)
  useEffect(() => {
    const el = contentRef.current
    if (!el) return
    const ro = new ResizeObserver(() => {
      if (isAtBottomRef.current) {
        scrollToBottom('auto')
      }
    })
    ro.observe(el)
    return () => ro.disconnect()
  }, [])

  // When a new message is added, force-scroll to bottom
  useEffect(() => {
    if (messageCount > prevCountRef.current) {
      isAtBottomRef.current = true
      requestAnimationFrame(() => scrollToBottom('smooth'))
    }
    prevCountRef.current = messageCount
  }, [messageCount])

  // When segments are added (e.g. plan.question), force-scroll to bottom
  useEffect(() => {
    if (segmentCount > prevSegCountRef.current) {
      isAtBottomRef.current = true
      requestAnimationFrame(() => scrollToBottom('auto'))
    }
    prevSegCountRef.current = segmentCount
  }, [segmentCount])

  // When streaming starts, force-scroll to bottom
  useEffect(() => {
    if (isStreaming) {
      isAtBottomRef.current = true
      scrollToBottom('auto')
    }
  }, [isStreaming])

  return { scrollRef, contentRef }
}
