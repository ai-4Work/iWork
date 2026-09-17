import { useState, useEffect, useRef } from 'react'

const TICK_MS = 50 // ~20fps, smooth but less overhead than 33ms

interface Props {
  text: string
  isStreaming: boolean
  onDone?: () => void
}

export function TypewriterText({ text, isStreaming, onDone }: Props) {
  const [revealedLen, setRevealedLen] = useState(() => {
    if (!isStreaming) return text.length
    return Math.max(0, Math.min(text.length, 200))
  })
  const timerRef = useRef<ReturnType<typeof setInterval>>()
  const doneRef = useRef(onDone)
  doneRef.current = onDone

  useEffect(() => {
    if (!isStreaming) {
      setRevealedLen(text.length)
      return
    }

    timerRef.current = setInterval(() => {
      setRevealedLen((prev) => {
        if (prev >= text.length) return prev
        // Reveal one full line per tick — jump to the next newline
        const nextNL = text.indexOf('\n', prev)
        if (nextNL >= 0) return nextNL + 1
        // Incomplete last line: reveal it all
        return text.length
      })
    }, TICK_MS)

    return () => clearInterval(timerRef.current)
  }, [text.length, isStreaming])

  useEffect(() => {
    if (!isStreaming) {
      setRevealedLen(text.length)
    }
  }, [isStreaming, text.length])

  useEffect(() => {
    if (revealedLen >= text.length) {
      doneRef.current?.()
    }
  }, [revealedLen, text.length])

  // Render plain text during the typewriter reveal — parsing markdown on every tick
  // (re-parsing the full growing text) is the dominant streaming CPU cost. The full
  // markdown render happens once `isStreaming` flips false.
  const displayText = text.slice(0, revealedLen)

  return (
    <div className="mt-1 mb-2 whitespace-pre-wrap break-words text-[#0f172a]">
      {displayText}
    </div>
  )
}
