import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import App from './App.vue'

describe('App', () => {
  it('renders the knowledge assistant shell', () => {
    const wrapper = mount(App)
    expect(wrapper.get('h1').text()).toBe('AI 知识库助手')
    expect(wrapper.text()).toContain('请选择或创建知识库')
  })
})
