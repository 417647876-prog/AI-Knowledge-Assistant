const LEGACY_PREFIX = 'ai-ka:conversation:'

export const conversationStorageKey = (userId: string, knowledgeBaseId: string) =>
  `${LEGACY_PREFIX}${userId}:${knowledgeBaseId}`

/**
 * 阶段 5 不再读取或写入旧浏览器会话。登录态清理时只删除当前用户的旧键，
 * 防止同一浏览器上其他账号的数据被误删。
 */
export function clearLegacyConversationsForUser(userId: string): void {
  const prefix = `${LEGACY_PREFIX}${userId}:`
  for (let index = sessionStorage.length - 1; index >= 0; index -= 1) {
    const key = sessionStorage.key(index)
    if (key?.startsWith(prefix)) sessionStorage.removeItem(key)
  }
}
