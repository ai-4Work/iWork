import type { ServerEvent } from '../types'

/**
 * Parse NDJSON (newline-delimited JSON) stream from a ReadableStream.
 * Each line is a complete JSON object representing a ServerEvent.
 */
export async function* parseNDJSONStream(
  reader: ReadableStreamDefaultReader<Uint8Array>
): AsyncGenerator<ServerEvent> {
  const decoder = new TextDecoder()
  let buffer = ''

  while (true) {
    const { done, value } = await reader.read()
    if (done) break

    buffer += decoder.decode(value, { stream: true })
    const lines = buffer.split('\n')
    buffer = lines.pop() || ''

    for (const line of lines) {
      const trimmed = line.trim()
      if (!trimmed) continue

      try {
        const event = JSON.parse(trimmed) as ServerEvent
        yield event
      } catch {
        // Skip malformed lines
      }
    }
  }

  // Process remaining buffer
  if (buffer.trim()) {
    try {
      const event = JSON.parse(buffer.trim()) as ServerEvent
      yield event
    } catch {
      // Skip malformed
    }
  }
}
