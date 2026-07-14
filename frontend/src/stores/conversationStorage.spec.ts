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
    { id: `question-${number}`, kind: 'user' as const, content: `question ${number}`, createdAt: 'now' },
    {
      id: `answer-${number}`, kind: 'assistant' as const, questionId: `question-${number}`,
      content: `answer ${number}`, createdAt: 'now', status: 'completed' as const, phase: null,
      citations: [], standaloneQuestion: null, retrievedChunkCount: null, timings: null,
      errorCode: null, requestId: null,
    },
  ]
}).flat()

const makeRound = (name: string, status: AssistantStatus): ConversationMessage[] => [
  { id: `question-${name}`, kind: 'user' as const, content: `question ${name}`, createdAt: 'now' },
  {
    id: `answer-${name}`, kind: 'assistant' as const, questionId: `question-${name}`,
    content: `answer ${name}`, createdAt: 'now', status, phase: null,
    citations: [], standaloneQuestion: null, retrievedChunkCount: null, timings: null,
    errorCode: null, requestId: null,
  },
]

describe('conversation storage', () => {
  beforeEach(() => sessionStorage.clear())

  it('keeps each knowledge base independent for the same user', () => {
    expect(conversationStorageKey('u-1', 'kb-1')).not.toBe(conversationStorageKey('u-2', 'kb-1'))
    const kb1Messages = makeRounds(1)
    const kb2Messages = makeRounds(2)

    saveConversation('u-1', 'kb-1', kb1Messages)
    saveConversation('u-1', 'kb-2', kb2Messages)

    expect(loadConversation('u-1', 'kb-1')).toEqual(kb1Messages)
    expect(loadConversation('u-1', 'kb-2')).toEqual(kb2Messages)
  })

  it('trims twenty-one rounds from question two and retains trailing messages', () => {
    const messages = [
      ...makeRounds(21),
      { id: 'post-question-21-divider', kind: 'divider' as const, createdAt: 'now' },
    ]

    const trimmed = trimConversation(messages)

    expect(trimmed[0]).toMatchObject({ id: 'question-2', content: 'question 2' })
    expect(trimmed).toContainEqual(expect.objectContaining({ id: 'answer-21', content: 'answer 21' }))
    expect(trimmed).toContainEqual(expect.objectContaining({ id: 'post-question-21-divider' }))
    expect(trimmed).not.toContainEqual(expect.objectContaining({ id: 'question-1' }))
    expect(trimmed).not.toContainEqual(expect.objectContaining({ id: 'answer-1' }))
  })

  it('builds the final six completed pairs after the last divider', () => {
    const messages = [
      ...makeRound('before-1', 'completed'),
      ...makeRound('before-2', 'completed'),
      { id: 'divider', kind: 'divider' as const, createdAt: 'now' },
      ...makeRound('post-1', 'completed'),
      ...makeRound('post-2', 'completed'),
      ...makeRound('post-3', 'completed'),
      ...makeRound('post-4', 'completed'),
      ...makeRound('post-5', 'completed'),
      ...makeRound('post-6', 'completed'),
      ...makeRound('post-7', 'completed'),
      ...makeRound('post-8', 'completed'),
    ]

    const history = buildHistory(messages)

    expect(history).toEqual([
      { role: 'user', content: 'question post-3' },
      { role: 'assistant', content: 'answer post-3' },
      { role: 'user', content: 'question post-4' },
      { role: 'assistant', content: 'answer post-4' },
      { role: 'user', content: 'question post-5' },
      { role: 'assistant', content: 'answer post-5' },
      { role: 'user', content: 'question post-6' },
      { role: 'assistant', content: 'answer post-6' },
      { role: 'user', content: 'question post-7' },
      { role: 'assistant', content: 'answer post-7' },
      { role: 'user', content: 'question post-8' },
      { role: 'assistant', content: 'answer post-8' },
    ])
    expect(history).not.toContainEqual(expect.objectContaining({ content: 'question before-1' }))
    expect(history).not.toContainEqual(expect.objectContaining({ content: 'answer before-1' }))
    expect(history).not.toContainEqual(expect.objectContaining({ content: 'question before-2' }))
    expect(history).not.toContainEqual(expect.objectContaining({ content: 'answer before-2' }))
    expect(history).not.toContainEqual(expect.objectContaining({ content: 'question post-1' }))
    expect(history).not.toContainEqual(expect.objectContaining({ content: 'answer post-1' }))
    expect(history).not.toContainEqual(expect.objectContaining({ content: 'question post-2' }))
    expect(history).not.toContainEqual(expect.objectContaining({ content: 'answer post-2' }))
  })

  it('excludes stopped and failed assistants from history', () => {
    expect(buildHistory([
      ...makeRound('one', 'completed'),
      ...makeRound('two', 'stopped'),
      ...makeRound('three', 'failed'),
    ])).toEqual([
      { role: 'user', content: 'question one' },
      { role: 'assistant', content: 'answer one' },
    ])
  })

  it('builds history from questions one through six before the specified question', () => {
    const history = buildHistory(makeRounds(8), 'question-7')

    expect(history).toEqual([
      { role: 'user', content: 'question 1' },
      { role: 'assistant', content: 'answer 1' },
      { role: 'user', content: 'question 2' },
      { role: 'assistant', content: 'answer 2' },
      { role: 'user', content: 'question 3' },
      { role: 'assistant', content: 'answer 3' },
      { role: 'user', content: 'question 4' },
      { role: 'assistant', content: 'answer 4' },
      { role: 'user', content: 'question 5' },
      { role: 'assistant', content: 'answer 5' },
      { role: 'user', content: 'question 6' },
      { role: 'assistant', content: 'answer 6' },
    ])
    expect(history).not.toContainEqual(expect.objectContaining({ content: 'question 7' }))
    expect(history).not.toContainEqual(expect.objectContaining({ content: 'answer 7' }))
    expect(history).not.toContainEqual(expect.objectContaining({ content: 'question 8' }))
    expect(history).not.toContainEqual(expect.objectContaining({ content: 'answer 8' }))
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
