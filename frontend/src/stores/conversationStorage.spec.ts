import { beforeEach, describe, expect, it } from 'vitest'
import {
  clearLegacyConversationsForUser,
  conversationStorageKey,
} from './conversationStorage'

describe('旧会话浏览器存储清理', () => {
  beforeEach(() => sessionStorage.clear())

  it('只删除当前用户的旧键，不误删其他用户或无关状态', () => {
    const currentOne = conversationStorageKey('u-1', 'kb-1')
    const currentTwo = conversationStorageKey('u-1', 'kb-2')
    const otherUser = conversationStorageKey('u-2', 'kb-1')
    sessionStorage.setItem(currentOne, '[{"content":"旧历史一"}]')
    sessionStorage.setItem(currentTwo, '[{"content":"旧历史二"}]')
    sessionStorage.setItem(otherUser, '[{"content":"其他用户历史"}]')
    sessionStorage.setItem('ai-ka:unrelated', '保留')

    clearLegacyConversationsForUser('u-1')

    expect(sessionStorage.getItem(currentOne)).toBeNull()
    expect(sessionStorage.getItem(currentTwo)).toBeNull()
    expect(sessionStorage.getItem(otherUser)).toContain('其他用户历史')
    expect(sessionStorage.getItem('ai-ka:unrelated')).toBe('保留')
  })
})
