/*
 * 中文说明（受管 variantorm 数据源的只读分析验收）：
 * 验证行为：在受治理的 variantorm MySQL 数据源上执行只读分析查询，校验真实返回结果。
 * 运行方式：需已配置该数据源的服务与浏览器；用例为原生（native）只读验收。
 * 边界：严格只读（不写业务库）；数据源须已授权且元数据已同步。
 */
/* Read-only native acceptance against the governed variantorm MySQL source. */
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const puppeteer = require('puppeteer-core')

const BASE = process.env.TEST5_E2E_BASE_URL || 'http://127.0.0.1:8012'
const PASSWORD = process.env.TEST5_E2E_PASSWORD
const SOURCE_ID = '01M0F1JBJBZY4QJZ7KHSSS0P01'
const OUTPUT = path.resolve('.implementation/ui-results/variantorm-analysis-' + Date.now().toString(36))
const SQL = "SELECT DATE_FORMAT(c_dingdanqiandingriqi, '%Y-%m') AS month, "
  + 'COUNT(*) AS order_count, COALESCE(SUM(c_xiaoshoudingdanjine), 0) AS sales_amount '
  + 'FROM t_xiaoshoudingdan WHERE isDeleted = 0 AND c_dingdanqiandingriqi IS NOT NULL '
  + "GROUP BY DATE_FORMAT(c_dingdanqiandingriqi, '%Y-%m') ORDER BY month"

async function clickText(frame, label, root = 'button') {
  const found = await frame.evaluate((label, root) => {
    const item = [...document.querySelectorAll(root)]
      .find(node => node.getClientRects().length && node.textContent.trim() === label)
    item?.click()
    return Boolean(item)
  }, label, root)
  assert(found, 'Visible control not found: ' + label)
}

(async () => {
  assert(PASSWORD, 'Native local password is required')
  assert.equal(process.env.TEST5_E2E_ALLOW_NATIVE, '1')
  assert(['127.0.0.1', 'localhost'].includes(new URL(BASE).hostname))
  const health = await fetch(BASE + '/api/health').then(response => response.json())
  assert.equal(health.app, 'native-dev')
  fs.mkdirSync(OUTPUT, { recursive: true })

  const browser = await puppeteer.launch({
    executablePath: process.env.TEST5_EDGE_PATH || 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',
    headless: true,
  })
  const page = await browser.newPage()
  await page.setViewport({ width: 1440, height: 960 })
  const failures = []
  page.on('pageerror', error => failures.push(error.message))
  page.on('response', response => { if (response.status() >= 500) failures.push(response.status() + ' ' + response.url()) })

  try {
    await page.goto(BASE, { waitUntil: 'networkidle0' })
    await page.type('#login-username', 'workspace-admin')
    await page.type('#login-password', PASSWORD)
    await page.click('#login-btn')
    await page.waitForSelector('.overview-intro')

    const route = '/analytics/sql?datasourceId=' + SOURCE_ID + '&sql=' + encodeURIComponent(SQL)
    await page.goto(BASE + route, { waitUntil: 'networkidle0', timeout: 60000 })
    const host = await page.waitForSelector('#business-module-frame')
    const frame = await host.contentFrame()
    await frame.waitForSelector('.sql-workspace')
    await frame.waitForFunction(() => !document.querySelector('.metadata-panel .el-loading-mask'))
    assert((await frame.$eval('.metadata-panel .el-select', node => node.textContent)).includes('variantorm'))

    const salesMetadata = await frame.evaluate(() => {
      const header = [...document.querySelectorAll('.el-collapse-item__header')]
        .find(item => item.textContent.trim() === '销售订单')
      header?.click()
      const item = header?.closest('.el-collapse-item')
      return { found: Boolean(header), text: item?.textContent || '' }
    })
    assert(salesMetadata.found)
    await frame.waitForFunction(() => [...document.querySelectorAll('.el-collapse-item')]
      .some(item => item.querySelector('.el-collapse-item__header')?.textContent.trim() === '销售订单'
        && item.textContent.includes('订单签订日期') && item.textContent.includes('销售订单金额')))

    const queried = page.waitForResponse(response => response.url().endsWith('/api/v1/analytics/query') && response.request().method() === 'POST')
    await clickText(frame, '执行查询')
    const response = await queried
    assert.equal(response.status(), 200)
    const result = (await response.json()).data
    assert.equal(result.datasourceId, SOURCE_ID)
    assert(result.totalCount > 0)
    assert.equal(result.enhancedAnalysis.timeDimension, 'month')
    assert.equal(result.chartRecommendations[0].chartType, 'line')
    assert.deepEqual(result.columns.map(item => item.columnName), ['month', 'order_count', 'sales_amount'])
    assert(result.data.every(row => /^\d{4}-\d{2}$/.test(row.month)))

    await frame.waitForSelector('.query-result-panel .el-table__row')
    await clickText(frame, '统计与洞察', '[role=tab]')
    await frame.waitForSelector('.enhanced-card')
    const insightText = await frame.$eval('.query-result-panel', node => node.textContent.replace(/\s+/g, ' '))
    assert(insightText.includes('趋势'))
    assert(insightText.includes('month'))
    await clickText(frame, '推荐图表', '[role=tab]')
    await frame.waitForSelector('canvas')

    await frame.addScriptTag({ path: require.resolve('axe-core/axe.min.js') })
    const serious = await frame.evaluate(async () => (await axe.run(document, {
      runOnly: { type: 'tag', values: ['wcag2a', 'wcag2aa', 'wcag21aa', 'wcag22aa'] },
    })).violations.filter(item => ['serious', 'critical'].includes(item.impact)))
    assert.deepEqual(serious, [])
    assert.deepEqual(failures, [])
    await page.screenshot({ path: path.join(OUTPUT, 'variantorm-real-monthly-sales.png'), fullPage: true })
    fs.writeFileSync(path.join(OUTPUT, 'result.json'), JSON.stringify({
      ok: true,
      sourceId: SOURCE_ID,
      queryId: result.queryId,
      rows: result.totalCount,
      timeDimension: result.enhancedAnalysis.timeDimension,
      chartType: result.chartRecommendations[0].chartType,
      columns: result.columns.map(item => ({ name: item.columnName, comment: item.comment })),
      accessibilitySerious: serious.length,
    }, null, 2))
    console.log('PASS variantorm metadata comments and explicit source selection')
    console.log('PASS real monthly sales SQL, temporal analysis and line-chart recommendation')
    console.log('PASS real result rendering and accessibility')
    console.log('RESULT_DIR ' + OUTPUT)
  } catch (error) {
    await page.screenshot({ path: path.join(OUTPUT, 'failure.png'), fullPage: true }).catch(() => {})
    throw error
  } finally {
    await browser.close()
  }
})().catch(error => {
  console.error(error.stack || error)
  process.exitCode = 1
})
