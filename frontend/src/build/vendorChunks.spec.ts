import { describe, expect, it } from 'vitest'
import { vendorChunkName } from './vendorChunks'

describe('Vite vendor chunk', () => {
  it.each([
    ['C:/repo/node_modules/vue/dist/vue.runtime.esm.js', 'vendor-vue'],
    ['C:/repo/node_modules/vue-router/dist/vue-router.mjs', 'vendor-vue'],
    ['C:/repo/node_modules/pinia/dist/pinia.mjs', 'vendor-vue'],
    ['C:/repo/node_modules/element-plus/es/index.mjs', 'vendor-element'],
    ['C:/repo/node_modules/@element-plus/icons-vue/dist/index.js', 'vendor-element'],
    ['C:/repo/node_modules/markdown-it/index.mjs', 'vendor-markdown'],
    ['C:/repo/node_modules/dompurify/dist/purify.es.mjs', 'vendor-markdown'],
    ['C:/repo/node_modules/other-package/index.js', 'vendor'],
    ['C:\\repo\\node_modules\\vue\\dist\\vue.runtime.esm.js', 'vendor-vue'],
    ['C:/repo/src/main.ts', undefined],
  ])('把 %s 稳定分到 %s', (id, expected) => {
    expect(vendorChunkName(id)).toBe(expected)
  })
})
