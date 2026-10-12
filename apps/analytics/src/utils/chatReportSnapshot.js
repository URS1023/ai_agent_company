/** 聊天预览只显示报表保存时的数据，不触发 SQL、HTTP 取数或自动刷新。 */
export function chatReportSnapshot(screen) {
  const snapshot = structuredClone(screen)
  let missing = 0
  const originals = new Map((screen.sourceContent?.widgets || []).map(widget => [String(widget.id), widget]))
  snapshot.refreshSeconds = 0
  snapshot.widgets = (snapshot.widgets || []).map(widget => {
    const config = widget.config || {}
    const remote = [2, 3].includes(Number(config.dataType))
    const original = originals.get(String(widget.id)) || widget
    let data = original.config?.chartDataParsed ?? original.config?.chartData ?? original.data ?? original.echartsOption?.dataset?.source
    if (data == null && original.value != null) data = [{ name: original.componentName || original.title, value: original.value, suffix: original.unit || '' }]
    if (!remote && data == null) data = config.chartDataParsed ?? config.chartData
    if (typeof data === 'string') {
      try { data = JSON.parse(data) } catch { data = undefined }
    }
    const saved = data != null && typeof data === 'object'
    // 空数组是有效的已保存空结果；缺失或无效数据不能补成零值。
    const embeddedPage = /Iframe|WebPage/.test(String(widget.component || ''))
    if ((remote && !saved) || embeddedPage) {
      missing += 1
      const text = `${widget.componentName || widget.title || '此组件'}\n${embeddedPage ? '网页组件请独立打开查看。' : '未保存预览数据，请独立打开查看。'}`
      return { ...widget, component: 'JText', title: text, config: { dataType: 1, chartDataParsed: [],
        option: { body: { text,
          fontSize: 20, color: '#a8bfd5', textAlign: 'center', lineHeight: 1.6 } } } }
    }
    const next = { ...widget, config: { ...config, dataType: 1, snapshotOnly: true,
      chartDataParsed: saved ? data : [], chartData: JSON.stringify(saved ? data : []) } }
    delete next.drilldown
    delete next.config.drilldown
    delete next.config.linkageConfig
    return next
  })
  return { screen: snapshot, missing }
}
