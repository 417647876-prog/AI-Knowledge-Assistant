export function vendorChunkName(id: string): string | undefined {
  const normalized = id.replace(/\\/g, '/')
  if (!normalized.includes('/node_modules/')) return undefined
  if (
    normalized.includes('/node_modules/element-plus/')
    || normalized.includes('/node_modules/@element-plus/icons-vue/')
  ) return 'vendor-element'
  if (
    normalized.includes('/node_modules/vue/')
    || normalized.includes('/node_modules/vue-router/')
    || normalized.includes('/node_modules/pinia/')
    || normalized.includes('/node_modules/@vueuse/')
  ) return 'vendor-vue'
  if (
    normalized.includes('/node_modules/markdown-it/')
    || normalized.includes('/node_modules/dompurify/')
  ) return 'vendor-markdown'
  return 'vendor'
}
