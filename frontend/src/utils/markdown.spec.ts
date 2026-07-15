import { describe, expect, it } from 'vitest'
import { renderSafeMarkdown } from './markdown'

describe('renderSafeMarkdown', () => {
  it('渲染标题、列表、表格、引用、链接和围栏代码块', () => {
    const html = renderSafeMarkdown(
      '# 标题\n\n- 项目\n\n|A|B|\n|-|-|\n|1|2|\n\n> 引用\n\n[链接](https://example.com)\n\n```csharp\nvar x = 1;\n```',
    )

    expect(html).toContain('<h1>标题</h1>')
    expect(html).toContain('<ul>')
    expect(html).toContain('<table>')
    expect(html).toContain('<blockquote>')
    expect(html).toContain('<code class="language-csharp">')
  })

  it('移除原始 HTML、脚本事件和危险 URL', () => {
    const html = renderSafeMarkdown('<img src=x onerror=alert(1)> [危险](javascript:alert(1))')
    const template = document.createElement('template')
    template.innerHTML = html

    expect(template.content.querySelector('img')).toBeNull()
    expect(template.content.querySelector('[onerror]')).toBeNull()
    expect(template.content.querySelector('a[href^="javascript:"]')).toBeNull()
  })

  it('为外部链接添加安全属性', () => {
    const html = renderSafeMarkdown('[外部](https://example.com)')

    expect(html).toContain('target="_blank"')
    expect(html).toContain('rel="noopener noreferrer"')
  })

  it('保留围栏代码块中的泛型尖括号', () => {
    const html = renderSafeMarkdown('```csharp\nList<string> values = [];\n```')

    expect(html).toContain('List&lt;string&gt; values = [];')
  })

  it('保留围栏代码中的危险链接文本', () => {
    const html = renderSafeMarkdown('```text\n](javascript:alert(1))\n```')

    expect(html).toContain('](javascript:alert(1))')
  })
})
