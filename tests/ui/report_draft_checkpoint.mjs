/*
 * 中文说明（报表草稿检查点的纯前端单元测试）：
 * 验证行为：加载后为「干净」、编辑后为「脏」、撤销全部编辑后重新变回干净（检查点回退语义）。
 * 运行方式：node --test tests/ui/report_draft_checkpoint.mjs；直接使用 apps/analytics 内的 Vue
 *   （需先在 apps/analytics 安装依赖）与 useDraftCheckpoint 组合函数。
 * 边界：纯组合函数测试，不连后端、不起浏览器。
 */
import assert from 'node:assert/strict'
import test from 'node:test'
import { reactive } from '../../apps/analytics/node_modules/vue/index.mjs'
import { useDraftCheckpoint } from '../../apps/analytics/src/composables/useDraftCheckpoint.js'

test('loading is clean, editing is dirty, undoing all edits is clean again', () => {
  const draft = reactive({ widgets: [{ x: 390 }], source: 'source-a' })
  const checkpoint = useDraftCheckpoint(() => draft)
  assert.equal(checkpoint.hasUnsavedChanges.value, false)
  checkpoint.markSaved()
  draft.widgets[0].x = 400
  assert.equal(checkpoint.hasUnsavedChanges.value, true)
  draft.widgets[0].x = 390
  assert.equal(checkpoint.hasUnsavedChanges.value, false)
  draft.source = 'source-b'
  assert.equal(checkpoint.hasUnsavedChanges.value, true)
})

test('successful save cannot mark later edits as persisted', async () => {
  const draft = reactive({ widgets: [{ x: 390 }] })
  const checkpoint = useDraftCheckpoint(() => draft)
  checkpoint.markSaved()
  draft.widgets[0].x = 400
  const submitted = checkpoint.capture()
  draft.widgets[0].x = 410
  await Promise.resolve()
  checkpoint.markSaved(submitted)
  assert.equal(checkpoint.hasUnsavedChanges.value, true)
  draft.widgets[0].x = 400
  assert.equal(checkpoint.hasUnsavedChanges.value, false)
})

test('failed save leaves dirty state intact, subsequent saved snapshot clears it', () => {
  const draft = reactive({ name: '原报表', widgets: [] })
  const checkpoint = useDraftCheckpoint(() => draft)
  checkpoint.markSaved()
  draft.name = '新名称'
  checkpoint.capture()
  assert.equal(checkpoint.hasUnsavedChanges.value, true)
  checkpoint.markSaved(checkpoint.capture())
  assert.equal(checkpoint.hasUnsavedChanges.value, false)
})
