import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'

import type { AssistantMessage } from '../types/conversation'
import RetrievalDetails from './RetrievalDetails.vue'

const message = (rewriteUsedFallback?: boolean): AssistantMessage => ({
  id: 'answer-1',
  kind: 'assistant',
  questionId: 'question-1',
  content: '回答',
  createdAt: '2026-07-15T00:00:00Z',
  status: 'completed',
  phase: null,
  citations: [],
  standaloneQuestion: '它呢？',
  rewriteUsedFallback,
  retrievedChunkCount: 1,
  timings: null,
  errorCode: null,
  requestId: 'request-1',
})

describe('RetrievalDetails', () => {
  it.each([
    [true, '已回退到原问题'],
    [false, '未回退'],
    [undefined, '未回退'],
  ] as const)('展示问题改写回退状态 %#', (usedFallback, expected) => {
    const wrapper = mount(RetrievalDetails, {
      props: { message: message(usedFallback) },
      global: {
        stubs: {
          ElCollapse: { template: '<div><slot /></div>' },
          ElCollapseItem: { template: '<section><slot /></section>' },
        },
      },
    })

    expect(wrapper.text()).toContain(expected)
  })
})
