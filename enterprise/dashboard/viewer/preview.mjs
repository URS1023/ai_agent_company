import { createDashboardTemplateContent } from '../lynx/src/reportDashboardTemplates.js'

export function preparePreview(templateId) {
  if (typeof templateId !== 'string') throw new Error('template_unavailable')
  const content = createDashboardTemplateContent(templateId)
  if (!content) throw new Error('template_unavailable')
  for (const widget of content.widgets) {
    const config = widget.config
    if (
      !config ||
      config.dataType !== 1 ||
      !Array.isArray(config.chartDataParsed) ||
      config.chartDataParsed.length !== 0 ||
      config.sql ||
      config.query ||
      config.url ||
      config.videoUrl ||
      config.imageUrl ||
      config.datasourceId
    ) {
      throw new Error('template_requires_data_only_review')
    }
  }
  return {
    page: {
      ...content.canvas,
      design: { width: content.canvas.width, height: content.canvas.height },
    },
    widgets: content.widgets,
  }
}
