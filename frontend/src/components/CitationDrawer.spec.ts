import { flushPromises, mount } from '@vue/test-utils'
import ElementPlus from 'element-plus'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import type { Citation } from '../types/api'
import CitationDrawer from './CitationDrawer.vue'

const citations: Citation[] = [{
  citation_id: 1,
  document_id: 'doc-1',
  file_name: '员工手册.md',
  content: '员工每年享有 10 天带薪年假。',
  relevance_score: 0.93,
  page_number: 3,
  sheet_name: null,
  row_start: null,
  section_title: '休假制度',
}]

describe('CitationDrawer', () => {
  beforeEach(() => {
    localStorage.clear()
    sessionStorage.clear()
  })

  afterEach(() => {
    document.body.replaceChildren()
  })

  it('在手机底部抽屉展示回答生成时的来源快照', async () => {
    mount(CitationDrawer, {
      attachTo: document.body,
      props: { modelValue: true, citations },
      global: { plugins: [ElementPlus] },
    })
    await flushPromises()

    const drawer = document.body.querySelector('[data-test="citation-drawer"]')
    expect(drawer?.textContent).toContain('引用来源')
    expect(drawer?.textContent).toContain('回答生成时保存的引用快照')
    expect(drawer?.textContent).toContain('员工手册.md')
    expect(drawer?.textContent).toContain('员工每年享有 10 天带薪年假。')
    expect(drawer?.textContent).toContain('第 3 页')
  })

  it('打开引用抽屉不会把引用或问答正文写入浏览器持久存储', async () => {
    mount(CitationDrawer, {
      attachTo: document.body,
      props: { modelValue: true, citations },
      global: { plugins: [ElementPlus] },
    })
    await flushPromises()

    expect(localStorage.length).toBe(0)
    expect(sessionStorage.length).toBe(0)
  })

  it('不完整引用快照不显示伪造相关度且仍能安全阅读', async () => {
    mount(CitationDrawer, {
      attachTo: document.body,
      props: {
        modelValue: true,
        citations: [{ ...citations[0]!, content: '引用快照正文不可用。', relevance_score: null }],
      },
      global: { plugins: [ElementPlus] },
    })
    await flushPromises()

    const drawerText = document.body.querySelector('[data-test="citation-drawer"]')?.textContent
    expect(drawerText).toContain('引用快照正文不可用。')
    expect(drawerText).not.toContain('相关度')
  })
})
