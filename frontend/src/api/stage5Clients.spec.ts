import { afterEach, describe, expect, it, vi } from 'vitest'
import {
  getMyQuota,
  getMyUsage,
  listMyFeedback,
  putMessageFeedback,
  removeMessageFeedback,
} from './me'
import {
  listTrash,
  purgeTrashDocument,
  purgeTrashKnowledgeBase,
  restoreTrashDocument,
  restoreTrashKnowledgeBase,
} from './trash'
import { createSupportGrant, listSupportGrants, revokeSupportGrant } from './supportGrants'
import {
  getOperationsJobs,
  getOperationsOverview,
  getOperationsQuality,
  listUserOperations,
} from './adminOperations'

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

afterEach(() => vi.unstubAllGlobals())

describe('阶段 5 普通用户客户端', () => {
  it('使用个人用量、额度和反馈端点及后端查询字段', async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(jsonResponse({}))
      .mockResolvedValueOnce(jsonResponse({}))
      .mockResolvedValueOnce(jsonResponse({ items: [], page: 2, page_size: 5, total: 0 }))
      .mockResolvedValueOnce(jsonResponse({}))
      .mockResolvedValueOnce(new Response(null, { status: 204 }))
    vi.stubGlobal('fetch', fetchMock)

    await getMyQuota()
    await getMyUsage('2026-07-01T00:00:00+08:00', '2026-07-20T00:00:00+08:00')
    await listMyFeedback({ page: 2, pageSize: 5 })
    await putMessageFeedback('message-1', { helpful: false, reason: 'unhelpful_missing' })
    await removeMessageFeedback('message-1')

    expect(fetchMock.mock.calls.map(([path, init]) => [path, init?.method])).toEqual([
      ['/api/v1/me/quota', undefined],
      ['/api/v1/me/usage?from=2026-07-01T00%3A00%3A00%2B08%3A00&to=2026-07-20T00%3A00%3A00%2B08%3A00', undefined],
      ['/api/v1/me/feedback?page=2&page_size=5', undefined],
      ['/api/v1/messages/message-1/feedback', 'PUT'],
      ['/api/v1/messages/message-1/feedback', 'DELETE'],
    ])
  })

  it('使用回收站恢复、彻底删除和支持授权端点', async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(jsonResponse({ knowledge_bases: [], documents: [] }))
      .mockResolvedValueOnce(new Response(null, { status: 204 }))
      .mockResolvedValueOnce(jsonResponse({ job_id: 'job-kb', status: 'pending' }, 202))
      .mockResolvedValueOnce(new Response(null, { status: 204 }))
      .mockResolvedValueOnce(jsonResponse({ job_id: 'job-doc', status: 'pending' }, 202))
      .mockResolvedValueOnce(jsonResponse({ id: 'grant-1' }, 201))
      .mockResolvedValueOnce(jsonResponse([]))
      .mockResolvedValueOnce(new Response(null, { status: 204 }))
    vi.stubGlobal('fetch', fetchMock)

    await listTrash()
    await restoreTrashKnowledgeBase('kb-1')
    await purgeTrashKnowledgeBase('kb-1')
    await restoreTrashDocument('doc-1')
    await purgeTrashDocument('doc-1')
    await createSupportGrant('kb-1', { admin_user_id: 'admin-1', expires_in_minutes: 30 })
    await listSupportGrants('kb-1')
    await revokeSupportGrant('grant-1')

    expect(fetchMock.mock.calls.map(([path, init]) => [path, init?.method])).toEqual([
      ['/api/v1/trash', undefined],
      ['/api/v1/knowledge-bases/kb-1/restore', 'POST'],
      ['/api/v1/knowledge-bases/kb-1/purge', 'DELETE'],
      ['/api/v1/documents/doc-1/restore', 'POST'],
      ['/api/v1/documents/doc-1/purge', 'DELETE'],
      ['/api/v1/knowledge-bases/kb-1/support-grants', 'POST'],
      ['/api/v1/knowledge-bases/kb-1/support-grants', undefined],
      ['/api/v1/support-grants/grant-1', 'DELETE'],
    ])
  })
})

describe('阶段 5 管理员运营客户端', () => {
  it('使用运营总览、用户、任务游标和质量端点', async () => {
    const fetchMock = vi.fn().mockImplementation(() => Promise.resolve(jsonResponse({})))
    vi.stubGlobal('fetch', fetchMock)

    const range = { startAt: '2026-07-01T00:00:00Z', endAt: '2026-07-20T00:00:00Z' }
    await getOperationsOverview(range)
    await listUserOperations(range)
    await getOperationsJobs({ ...range, limit: 50, cursorCreatedAt: '2026-07-19T00:00:00Z', cursorId: 'job-1' })
    await getOperationsQuality(range)

    expect(fetchMock.mock.calls.map(([path]) => path)).toEqual([
      '/api/v1/admin/operations/overview?start_at=2026-07-01T00%3A00%3A00Z&end_at=2026-07-20T00%3A00%3A00Z',
      '/api/v1/admin/operations/users?start_at=2026-07-01T00%3A00%3A00Z&end_at=2026-07-20T00%3A00%3A00Z',
      '/api/v1/admin/operations/jobs?start_at=2026-07-01T00%3A00%3A00Z&end_at=2026-07-20T00%3A00%3A00Z&limit=50&cursor_created_at=2026-07-19T00%3A00%3A00Z&cursor_id=job-1',
      '/api/v1/admin/operations/quality?start_at=2026-07-01T00%3A00%3A00Z&end_at=2026-07-20T00%3A00%3A00Z',
    ])
  })
})
