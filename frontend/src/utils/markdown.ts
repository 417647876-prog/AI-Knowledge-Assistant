import DOMPurify from 'dompurify'
import MarkdownIt from 'markdown-it'

const markdown = new MarkdownIt({ html: false, linkify: true, breaks: true })
const safeProtocols = new Set(['http:', 'https:', 'mailto:'])

export function renderSafeMarkdown(source: string): string {
  const template = document.createElement('template')
  const markdownSource = source.replace(
    /\]\(\s*(?:javascript|vbscript|data)\s*:[^)]*\)/gi,
    ']',
  )
  template.innerHTML = DOMPurify.sanitize(markdown.render(markdownSource))

  for (const link of template.content.querySelectorAll('a[href]')) {
    const href = link.getAttribute('href') ?? ''
    let url: URL
    try {
      url = new URL(href, window.location.href)
    } catch {
      link.removeAttribute('href')
      continue
    }
    if (!safeProtocols.has(url.protocol)) {
      link.removeAttribute('href')
      continue
    }
    if (url.origin !== window.location.origin) {
      link.setAttribute('target', '_blank')
      link.setAttribute('rel', 'noopener noreferrer')
    }
  }

  return template.innerHTML
}
