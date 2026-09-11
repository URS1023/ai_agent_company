import { createApp } from 'vue'
import { preparePreview } from './preview.mjs'
import Preview from './Preview.vue'

let content = null
try {
  content = preparePreview(
    new URLSearchParams(window.location.search).get('template') || 'equipment-digital-ops',
  )
} catch {
  // Unknown template identifiers render the existing localized error state.
}
createApp(Preview, { content }).mount('#app')
