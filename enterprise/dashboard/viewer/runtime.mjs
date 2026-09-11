import { createApp, h, shallowRef } from 'vue'
import { prepareDashboardView } from './api-data.mjs'
import Preview from './Preview.vue'
import { applySeriesBatch } from './series-data.mjs'

export function mountDashboardView(element, initial, expected) {
  const context = { ...expected }
  const state = shallowRef(prepareDashboardView(initial, context))
  let disposed = false
  const app = createApp({
    render: () =>
      h(Preview, {
        content: state.value.content,
        status: state.value.status,
      }),
  })
  app.mount(element)
  return {
    update(next) {
      if (disposed) throw new Error('dashboard_disposed')
      const prepared = prepareDashboardView(next, context)
      if (prepared.revision <= state.value.revision) throw new Error('dashboard_revision_stale')
      state.value = prepared
    },
    dispose() {
      if (disposed) return
      disposed = true
      app.unmount()
    },
  }
}

export function mountDashboard(element, initial) {
  const content = shallowRef(applySeriesBatch(initial.templateId, initial.batch))
  let disposed = false
  const app = createApp({ render: () => h(Preview, { content: content.value }) })
  app.mount(element)
  return {
    update(next) {
      if (disposed) throw new Error('dashboard_disposed')
      const prepared = applySeriesBatch(next.templateId, next.batch)
      content.value = prepared
    },
    dispose() {
      if (disposed) return
      disposed = true
      app.unmount()
    },
  }
}
