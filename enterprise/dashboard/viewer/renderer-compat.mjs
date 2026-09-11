// Keep the imported source snapshot intact; fail on upstream formatter drift.
export function preserveMetricPrecision(source) {
  const legacy = "return value.toLocaleString('zh-CN', { maximumFractionDigits: 1 })"
  if (source.split(legacy).length !== 2) throw new Error('renderer_formatter_changed')
  return source.replace(legacy, 'return String(value)')
}

export function locateRendererAssets(source) {
  const legacy = 'fetch(`${import.meta.env.BASE_URL}jimu-screen/china.json`)'
  if (source.split(legacy).length !== 2) throw new Error('renderer_asset_locator_changed')
  return source.replace(
    legacy,
    "fetch(new URL(/* @vite-ignore */ '../jimu-screen/china.json', import.meta.url))",
  )
}
