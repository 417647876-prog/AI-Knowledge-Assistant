import type {
  AssistantMessage,
  ConversationHistory,
  ConversationMessage,
} from '../types/conversation'

const PREFIX = 'ai-ka:conversation:'

export const conversationStorageKey = (userId: string, knowledgeBaseId: string) =>
  `${PREFIX}${userId}:${knowledgeBaseId}`

export function trimConversation(messages: ConversationMessage[], limit = 20): ConversationMessage[] {
  const userIndexes = messages.flatMap((item, index) => item.kind === 'user' ? [index] : [])
  if (userIndexes.length <= limit) return messages
  return messages.slice(userIndexes[userIndexes.length - limit]!)
}

export function buildHistory(
  messages: ConversationMessage[],
  beforeQuestionId?: string,
): ConversationHistory[] {
  const targetIndex = beforeQuestionId
    ? messages.findIndex((item) => item.kind === 'user' && item.id === beforeQuestionId)
    : messages.length
  const prefix = messages.slice(0, targetIndex >= 0 ? targetIndex : messages.length)
  const lastDivider = prefix.reduce(
    (last, item, index) => item.kind === 'divider' ? index : last,
    -1,
  )
  const scoped = prefix.slice(lastDivider + 1)
  const pairs: ConversationHistory[][] = []

  for (const question of scoped) {
    if (question.kind !== 'user') continue
    const answer = scoped.find((item): item is AssistantMessage =>
      item.kind === 'assistant' && item.questionId === question.id,
    )
    if (answer?.status === 'completed') {
      pairs.push([
        { role: 'user', content: question.content },
        { role: 'assistant', content: answer.content },
      ])
    }
  }

  return pairs.slice(-6).flat()
}

export function loadConversation(userId: string, knowledgeBaseId: string): ConversationMessage[] {
  const key = conversationStorageKey(userId, knowledgeBaseId)
  const raw = sessionStorage.getItem(key)
  if (!raw) return []

  try {
    const restored = (JSON.parse(raw) as ConversationMessage[]).map((item) =>
      item.kind === 'assistant' && item.status === 'streaming'
        ? { ...item, status: 'stopped' as const, phase: null }
        : item,
    )
    return trimConversation(restored)
  } catch {
    sessionStorage.removeItem(key)
    return []
  }
}

export function saveConversation(
  userId: string,
  knowledgeBaseId: string,
  messages: ConversationMessage[],
): void {
  const key = conversationStorageKey(userId, knowledgeBaseId)
  if (!messages.length) {
    sessionStorage.removeItem(key)
    return
  }
  sessionStorage.setItem(key, JSON.stringify(trimConversation(messages)))
}

export function clearUserConversations(userId: string): void {
  const prefix = `${PREFIX}${userId}:`
  for (let index = sessionStorage.length - 1; index >= 0; index -= 1) {
    const key = sessionStorage.key(index)
    if (key?.startsWith(prefix)) sessionStorage.removeItem(key)
  }
}
