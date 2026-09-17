export interface SSEDelta {
  content?: string
  tool_calls?: Array<{
    index: number
    id?: string
    function?: {
      name?: string
      arguments?: string
    }
  }>
}

export interface SSEChunk {
  content: string
  toolCalls: Map<number, {
    id: string
    name: string
    arguments: string
    complete: boolean
  }>
  done: boolean
}

export async function* parseSSEStream(
  reader: ReadableStreamDefaultReader<Uint8Array>
): AsyncGenerator<SSEChunk> {
  const decoder = new TextDecoder()
  let buffer = ''
  const accumulatingToolCalls = new Map<number, {
    id: string
    name: string
    arguments: string
    complete: boolean
  }>()

  while (true) {
    const { done, value } = await reader.read()
    if (done) break

    buffer += decoder.decode(value, { stream: true })
    const lines = buffer.split('\n')
    buffer = lines.pop() || ''

    for (const line of lines) {
      const trimmed = line.trim()
      if (!trimmed || !trimmed.startsWith('data: ')) continue

      const data = trimmed.slice(6)
      if (data === '[DONE]') {
        yield { content: '', toolCalls: accumulatingToolCalls, done: true }
        return
      }

      try {
        const parsed = JSON.parse(data)
        const choice = parsed.choices?.[0]
        if (!choice) continue

        const delta: SSEDelta = choice.delta || {}
        let content = ''

        if (delta.content) {
          content = delta.content
        }

        if (delta.tool_calls) {
          for (const tc of delta.tool_calls) {
            const existing = accumulatingToolCalls.get(tc.index) || {
              id: tc.id || '',
              name: '',
              arguments: '',
              complete: false
            }
            if (tc.id) existing.id = tc.id
            if (tc.function?.name) existing.name = tc.function.name
            if (tc.function?.arguments) existing.arguments += tc.function.arguments
            accumulatingToolCalls.set(tc.index, existing)
          }
        }

        yield { content, toolCalls: accumulatingToolCalls, done: false }
      } catch {
        // skip malformed JSON lines
      }
    }
  }
}
