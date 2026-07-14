import { beforeEach, describe, expect, it } from 'vitest'
import type { AssistantStatus, ConversationMessage } from '../types/conversation'
import {
  buildHistory,
  clearUserConversations,
  loadConversation,
  saveConversation,
  conversationStorageKey,
  trimConversation,
} from './conversationStorage'

const makeRounds = (count: number): ConversationMessage[] => Array.from({ length: count }, (_, index) => {
  const number = index + 1
  return [
    { id: `question-${number}`, kind: 'user' as const, content: `问题 ${number}`, createdAt: 'now' },
    {
      id: `answer-${number}`, kind: 'assistant' as const, questionId: `question-${number}`,
      content: `答案 ${number}`, createdAt: 'now', status: 'completed' as const, phase: null,
      citations: [], standaloneQuestion: null, retrievedChunkCount: null, timings: null,
      errorCode: null, requestId: null,
    },
  ]
}).flat()

const makeRound = (name: string, status: AssistantStatus) => [
  { id: `question-${name}`, kind: 'user' as const, content: `问题 ${name}`, createdAt: 'now' },
  {
    id: `answer-${name}`, kind: 'assistant' as const, questionId: `question-${name}`,
    content: `答案 ${name}`, createdAt: 'now', status, phase: null,
    citations: [], standaloneQuestion: null, retrievedChunkCount: null, timings: null,
    errorCode: null, requestId: null,
  },
]

describe('conversation storage', () => {
  beforeEach(() => sessionStorage.clear())

  it('uses user and knowledge base in the session key and trims to twenty rounds', () => {
    expect(conversationStorageKey('u-1', 'kb-1')).not.toBe(conversationStorageKey('u-2', 'kb-1'))
    expect(trimConversation(makeRounds(21)).filter((item) => item.kind === 'user')).toHaveLength(20)
  })

  it('builds at most six completed pairs after the last divider', () => {
    const messages = makeRounds(8)
    messages.splice(4, 0, { id: 'divider', kind: 'divider' as const, createdAt: 'now' })

    const history = buildHistory(messages)

    expect(history).toHaveLength(12)
    expect(history[0]).toEqual({ role: 'user', content: '问题 3' })
  })

  it('excludes stopped and failed assistants from history', () => {
    expect(buildHistory([
      ...makeRound('one', 'completed'),
      ...makeRound('two', 'stopped'),
      ...makeRound('three', 'failed'),
    ])).toEqual([
      { role: 'user', content: '问题 one' },
      { role: 'assistant', content: '答案 one' },
    ])
  })

  it('builds history only from messages before the specified question', () => {
    expect(buildHistory(makeRounds(8), 'question-7')).toHaveLength(12)
  })

  it('restores an interrupted stream as stopped after a refresh', () => {
    saveConversation('u', 'kb', makeRound('refresh', 'streaming'))

    const restored = loadConversation('u', 'kb')
    expect(restored[restored.length - 1]).toMatchObject({
      status: 'stopped', phase: null,
    })
  })

  it('removes corrupted storage rather than exposing invalid conversation data', () => {
    const key = conversationStorageKey('u', 'kb')
    sessionStorage.setItem(key, '{broken')

    expect(loadConversation('u', 'kb')).toEqual([])
    expect(sessionStorage.getItem(key)).toBeNull()
  })

  it('removes empty conversations and only clears the requested users conversations', () => {
    saveConversation('u-1', 'kb-1', makeRounds(1))
    saveConversation('u-1', 'kb-2', makeRounds(1))
    saveConversation('u-2', 'kb-1', makeRounds(1))
    saveConversation('u-1', 'kb-1', [])

    expect(sessionStorage.getItem(conversationStorageKey('u-1', 'kb-1'))).toBeNull()
    clearUserConversations('u-1')

    expect(sessionStorage.getItem(conversationStorageKey('u-1', 'kb-2'))).toBeNull()
    expect(sessionStorage.getItem(conversationStorageKey('u-2', 'kb-1'))).not.toBeNull()
  })
})
