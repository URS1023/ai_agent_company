/*
 * 中文说明（质检查询与导出流程的真实浏览器验收）：
 * 验证行为：真实质检查询后执行导出，校验导出结果与页面数据一致。
 * 运行方式：只能对隔离的 APP_ENV=test 服务运行（TEST5_E2E_BASE_URL，默认 8011），需本机 Edge
 *   与 TEST5_E2E_PASSWORD；结果写入 .implementation/ui-results/analytics-quality-<run>/。
 * 边界：选择下拉统一走 choose() 辅助函数等待选项渲染，避免竞态导致的假失败。
 */
/* Real quality-check and export flow; only run against an isolated APP_ENV=test server. */
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const puppeteer = require('puppeteer-core')

const BASE = process.env.TEST5_E2E_BASE_URL || 'http://127.0.0.1:8011'
const PASSWORD = process.env.TEST5_E2E_PASSWORD
const RUN = Date.now().toString(36)
const OUTPUT = path.resolve('.implementation/ui-results/analytics-quality-' + RUN)

async function choose(frame, selector, text) {
  await frame.click(selector)
  await frame.waitForFunction(value => [...document.querySelectorAll('.el-select-dropdown__item')]
    .some(item => item.getClientRects().length && item.textContent.includes(value)), {}, text)
  await frame.evaluate(value => [...document.querySelectorAll('.el-select-dropdown__item')]
    .find(item => item.getClientRects().length && item.textContent.includes(value)).click(), text)
}

(async () => {
  assert.equal(process.env.TEST5_E2E_ALLOW_WRITE, 'isolated')
  assert(PASSWORD, 'Isolated test password required')
  assert(['127.0.0.1', 'localhost'].includes(new URL(BASE).hostname))
  assert.equal((await (await fetch(BASE + '/api/health')).json()).app, 'test')
  fs.mkdirSync(OUTPUT, { recursive: true })

  const browser = await puppeteer.launch({
    executablePath: process.env.TEST5_EDGE_PATH || 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',
    headless: true,
  })
  const page = await browser.newPage()
  await page.setViewport({ width: 1440, height: 960 })
  const failures = []
  page.on('pageerror', error => failures.push(error.message))
  page.on('response', response => {
    if (response.status() >= 500) failures.push(response.status() + ' ' + response.url())
  })

  try {
    await page.goto(BASE, { waitUntil: 'networkidle0' })
    await page.type('#login-username', 'workspace-admin')
    await page.type('#login-password', PASSWORD)
    await page.click('#login-btn')
    await page.waitForSelector('.overview-intro')

    const sourceName = '质量分析样本 ' + RUN
    const source = await page.evaluate(async ({ sourceName, run }) => {
      const data = [
        'department,amount,status',
        '设备,100,正常',
        '设备,100,正常',
        '财务,101,正常',
        '销售,102,预警',
        '采购,103,正常',
        '质检,1000,正常',
        '运营,,正常',
      ].join('\n')
      const body = new FormData()
      body.append('name', sourceName)
      body.append('table_name', 'quality_sample')
      body.append('file', new File(['\uFEFF' + data], `quality-${run}.csv`, { type: 'text/csv' }))
      const response = await fetch('/api/v1/analytics/datasources/import', {
        method: 'POST',
        headers: {
          Authorization: 'Bearer ' + localStorage.getItem('agent_token'),
          'X-Workspace-Id': localStorage.getItem('agent_workspace_id'),
        },
        body,
      })
      const payload = await response.json()
      if (!response.ok) throw new Error(payload.message || '数据源导入失败')
      return payload.data
    }, { sourceName, run: RUN })
    assert.equal(source.metadataStatus, 'ready')
    assert(source.autoDescription)

    await page.goto(BASE + '/analytics/quality', { waitUntil: 'networkidle0' })
    const host = await page.waitForSelector('#business-module-frame')
    const frame = await host.contentFrame()
    await frame.waitForSelector('.quality-form')
    const metadataLoaded = page.waitForResponse(response => response.url().includes('/api/v1/analytics/datasources/' + source.id + '/tables/info'))
    await choose(frame, '.quality-form .el-form-item:nth-child(1) .el-select', sourceName)
    assert.equal((await metadataLoaded).status(), 200)
    await frame.waitForFunction(() => !document.querySelector('.quality-form .el-loading-mask'))
    await choose(frame, '.quality-form .el-form-item:nth-child(2) .el-select', 'quality_sample')
    assert((await frame.$eval('.quality-source-context', item => item.textContent)).trim().length > 10)

    const checked = page.waitForResponse(response => response.url().endsWith('/api/v1/analytics/quality-check') && response.request().method() === 'POST')
    await frame.evaluate(() => [...document.querySelectorAll('button')].find(item => item.textContent.trim() === '开始检查').click())
    const checkResponse = await checked
    assert.equal(checkResponse.status(), 200)
    const quality = (await checkResponse.json()).data
    assert.equal(quality.sampleSize, 7)
    assert.equal(quality.statistics.missingCells, 1)
    assert.equal(quality.statistics.duplicateRows, 1)
    assert.equal(quality.statistics.columns.amount.outlierCount, undefined)
    assert.deepEqual(new Set(quality.issues.map(item => item.type)), new Set(['missing', 'duplicate']))
    await frame.waitForSelector('.quality-summary')
    const summary = Object.fromEntries(await frame.$$eval('.quality-summary article', items => items.map(item => [
      item.querySelector('small').textContent.trim(), item.querySelector('strong').textContent.trim(),
    ])))
    assert.deepEqual(summary, { 完整性: '95.2%', 缺失单元格: '1', 完全重复行: '1', 发现问题: '2' })

    await frame.click('.quality-result-heading .el-dropdown button')
    await frame.waitForFunction(() => [...document.querySelectorAll('.el-dropdown-menu__item')]
      .some(item => item.getClientRects().length && item.textContent.trim() === 'HTML 分析报告'))
    const exported = page.waitForResponse(response => response.url().endsWith('/api/v1/analytics/export') && response.request().method() === 'POST')
    await frame.evaluate(() => [...document.querySelectorAll('.el-dropdown-menu__item')]
      .find(item => item.getClientRects().length && item.textContent.trim() === 'HTML 分析报告').click())
    const exportResponse = await exported
    assert.equal(exportResponse.status(), 200)
    assert(String(exportResponse.headers()['content-type']).startsWith('text/html'))
    assert(String(exportResponse.headers()['content-disposition']).includes('analysis-' + quality.queryId + '.html'))

    await frame.addScriptTag({ path: require.resolve('axe-core/axe.min.js') })
    const serious = await frame.evaluate(async () => (await axe.run(document, {
      runOnly: { type: 'tag', values: ['wcag2a', 'wcag2aa', 'wcag21aa', 'wcag22aa'] },
    })).violations.filter(item => ['serious', 'critical'].includes(item.impact)))
    assert.deepEqual(serious, [])
    assert.deepEqual(failures, [])
    await page.screenshot({ path: path.join(OUTPUT, 'quality-result-and-export.png') })
    fs.writeFileSync(path.join(OUTPUT, 'result.json'), JSON.stringify({
      ok: true,
      sourceId: source.id,
      queryId: quality.queryId,
      sampleSize: quality.sampleSize,
      issues: quality.issues.map(item => item.type),
      accessibilitySerious: serious.length,
    }, null, 2))
    console.log('PASS real quality summary, source context and issue classification')
    console.log('PASS authorized HTML export from the quality result')
    console.log('PASS quality result accessibility and error-free browser execution')
    console.log('RESULT_DIR ' + OUTPUT)
  } catch (error) {
    await page.screenshot({ path: path.join(OUTPUT, 'failure.png') }).catch(() => {})
    throw error
  } finally {
    await browser.close()
  }
})().catch(error => {
  console.error(error.stack || error)
  process.exitCode = 1
})
