import { DASHBOARD_TEMPLATES } from './lynx/src/reportDashboardTemplates.js'
import { preparePreview } from './viewer/preview.mjs'
import { supportsSeriesComponent } from './viewer/series-data.mjs'

export function createTemplateCatalog(rendererBuildId, resources) {
  if (
    !/^[a-f0-9]{64}$/.test(rendererBuildId) ||
    !Array.isArray(resources) ||
    !resources.length ||
    resources.some(
      (item) => typeof item.name !== 'string' || !item.name || !/^[a-f0-9]{64}$/.test(item.sha256),
    ) ||
    new Set(resources.map((item) => item.name)).size !== resources.length
  )
    throw new Error('invalid_renderer_resources')
  return {
    schema_version: 1,
    templates: DASHBOARD_TEMPLATES.map((template) => {
      const visual = preparePreview(template.id)
      return {
        template_id: template.id,
        template_revision: 1,
        design_revision: 1,
        visual_json: JSON.stringify(visual),
        renderer_build_id: rendererBuildId,
        component_schema_version: 'lynx-series-v1',
        asset_digests: resources.map((item) => ({ name: item.name, sha256: item.sha256 })),
        font_digests: [],
        slots: visual.widgets
          .filter((widget) => supportsSeriesComponent(widget.component))
          .map((widget) => ({
            slot_id: widget.id,
            columns: [
              { name: 'name', kind: 'string', unit: null, required: true, nullable: false },
              { name: 'value', kind: 'decimal', unit: null, required: true, nullable: false },
            ],
            row_limit: 1000,
            required: false,
          })),
      }
    }),
  }
}
