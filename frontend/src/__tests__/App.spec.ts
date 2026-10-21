import { describe, it, expect } from 'vitest'

import { mount } from '@vue/test-utils'
import ChatView from '../views/ChatView.vue'

describe('ChatView', () => {
  it('renders chat page', () => {
    const wrapper = mount(ChatView)
    expect(wrapper.text()).toContain('问答助手')
    expect(wrapper.find('textarea').exists()).toBe(true)
  })
})
