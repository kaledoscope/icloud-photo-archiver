// 真机跑一遍界面全流程：填目录 → 扫描 → 审阅 → 命名 → 预演
//
// 这是开发期的端到端验证脚本，不是交付物的一部分。
//
// 用法（需要一个已经在跑的实例）：
//
//     npm i -D puppeteer-core                      # 首次
//     PHOTO_SRC=~/Pictures/some_album \
//     PHOTO_OUT=/tmp/ui-test \
//     node web/tests/ui-smoke.mjs http://127.0.0.1:8787
//
// 环境变量：
//     PHOTO_SRC   源目录（必填）—— 里面最好同时含「映射表里有的设备」和
//                 「读不出机型的文件」，这样命名弹窗的两个分支都能走到
//     PHOTO_OUT   输出目录（默认 /tmp/ui-test）
//     CHROME_BIN  Chrome 可执行文件（默认 macOS 标准安装位置）
//
// ⚠️ 打 8787 会写真实的 config/device-models.json。
//    想验证命名写回又不想动真配置，就把 DEVICE_MODELS_PATH 指向一份副本，
//    另起一个实例（例如 8788）再把这个脚本指过去。
import puppeteer from 'puppeteer-core'

const SRC = process.env.PHOTO_SRC
const OUT = process.env.PHOTO_OUT ?? '/tmp/ui-test'
const CHROME = process.env.CHROME_BIN ?? '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'

if (!SRC) {
  console.error('缺少 PHOTO_SRC。例：PHOTO_SRC=~/Pictures/some_album node web/tests/ui-smoke.mjs')
  process.exit(2)
}

const BASE = process.argv[2] ?? 'http://127.0.0.1:8787'

const browser = await puppeteer.launch({
  executablePath: CHROME,
  headless: 'new',
  args: ['--no-sandbox', '--disable-gpu'],
})

const page = await browser.newPage()
await page.setViewport({ width: 1280, height: 1000, deviceScaleFactor: 2 })

const errors = []
page.on('console', (m) => {
  if (m.type() === 'error') errors.push(`console: ${m.text()}`)
})
page.on('pageerror', (e) => errors.push(`pageerror: ${e.message}`))
page.on('requestfailed', (r) => errors.push(`requestfailed: ${r.url()} ${r.failure()?.errorText}`))

const step = (s) => console.log(`\n▸ ${s}`)
const wait = (ms) => new Promise((r) => setTimeout(r, ms))

async function clickByText(text) {
  for (const b of await page.$$('button')) {
    const t = await b.evaluate((el) => el.textContent)
    if (t.includes(text)) {
      await b.click()
      return true
    }
  }
  return false
}

try {
  step('打开页面')
  await page.goto(`${BASE}/`, { waitUntil: 'networkidle0' })
  console.log(`  标题: ${await page.$eval('h1', (el) => el.textContent)}`)
  console.log(`  服务状态: ${await page.$eval('.health', (el) => el.textContent.trim())}`)

  // 工具缺失会静默改变分类结果，先报出来，免得后面白跑
  const banners0 = await page.$$eval('.banner', (els) => els.map((e) => e.textContent.replace(/\s+/g, ' ').trim()))
  if (banners0.length) banners0.forEach((b) => console.log(`  ⚠️ 提示条: ${b}`))

  step('填入源目录与输出目录')
  const inputs = await page.$$('input[type=text]')
  await inputs[0].click({ clickCount: 3 })
  await inputs[0].type(SRC)
  await inputs[1].click({ clickCount: 3 })
  await inputs[1].type(OUT)
  await page.screenshot({ path: '/tmp/ui-1-filled.png' })

  step('点击「开始扫描」')
  if (!(await clickByText('开始扫描'))) throw new Error('找不到「开始扫描」按钮')

  step('等待计划出现')
  await page.waitForFunction(
    () => [...document.querySelectorAll('h2')].some((h) => h.textContent.includes('审阅计划')),
    { timeout: 180000 },
  )
  await wait(1500)

  console.log('  统计卡片:')
  ;(await page.$$eval('.stat', (els) =>
    els.map((e) => `${e.querySelector('.n').textContent.trim()} ${e.querySelector('.l').textContent.trim()}`),
  )).forEach((s) => console.log(`    ${s}`))

  console.log('  设备分组:')
  ;(await page.$$eval('.dev', (els) =>
    els.map(
      (e) =>
        e.querySelector('.devname').textContent.trim() +
        ' | ' +
        [...e.querySelectorAll('.pill')].map((p) => p.textContent.trim()).join(' '),
    ),
  )).forEach((d) => console.log(`    ${d}`))

  console.log(`  计划表格行数: ${await page.$$eval('tbody tr', (r) => r.length)}`)

  const banners = await page.$$eval('.banner', (els) => els.map((e) => e.textContent.replace(/\s+/g, ' ').trim()))
  if (banners.length) {
    console.log('  提示条:')
    banners.forEach((b) => console.log(`    ${b}`))
  }
  await page.screenshot({ path: '/tmp/ui-2-plan.png', fullPage: true })

  step('按设备筛选')
  const devCards = await page.$$('.dev')
  console.log(`  设备卡片数: ${devCards.length}`)
  await devCards[0].click()
  await wait(900)
  console.log(`  筛选条: ${await page.$eval('.filters', (e) => e.textContent.replace(/\s+/g, ' ').trim())}`)
  console.log(`  本页渲染行数: ${await page.$$eval('tbody tr', (r) => r.length)}`)
  await devCards[0].click()
  await wait(700)
  console.log(`  取消筛选后: ${await page.$eval('.filters', (e) => e.textContent.replace(/\s+/g, ' ').trim())}`)

  step('分页')
  const pagerText = await page.$eval('.pager', (e) => e.textContent.replace(/\s+/g, ' ').trim()).catch(() => '（无分页，条目不足一页）')
  console.log(`  分页控件: ${pagerText}`)
  if (pagerText.includes('下一页')) {
    await clickByText('下一页')
    await wait(900)
    console.log(`  翻到第 2 页，行数: ${await page.$$eval('tbody tr', (r) => r.length)}`)
    console.log(`  分页控件: ${await page.$eval('.pager', (e) => e.textContent.replace(/\s+/g, ' ').trim())}`)
    await clickByText('上一页')
    await wait(700)
  }

  step('打开设备命名弹窗')
  if (!(await clickByText('设备命名'))) throw new Error('找不到「设备命名」按钮')
  await page.waitForSelector('[role=dialog]', { timeout: 10000 })
  await wait(1200)
  console.log('  弹窗内容:')
  console.log('   ', (await page.$eval('[role=dialog]', (e) => e.textContent.replace(/\s+/g, ' ').trim())).slice(0, 400))
  await page.screenshot({ path: '/tmp/ui-3-naming.png' })

  step('给「读不出机型」的文件命名并保存')
  const dlgInputs = await page.$$('[role=dialog] input[type=text]')
  console.log(`  弹窗内输入框数: ${dlgInputs.length}`)
  if (dlgInputs.length > 0) {
    const last = dlgInputs[dlgInputs.length - 1]
    await last.click()
    await last.evaluate((el) => el.select())   // 先全选再输入，否则会追加而不是替换
    await last.type('Old_Phone_2019')
    await clickByText('保存')
    await wait(1500)
    const bodyText = await page.$eval('[role=dialog]', (e) => e.textContent.replace(/\s+/g, ' ').trim())
    const okMatch = bodyText.match(/已记下[^。]*/)
    console.log(`  保存反馈: ${okMatch ? okMatch[0] : '（没看到成功提示）'}`)
    await page.screenshot({ path: '/tmp/ui-3b-named.png' })
  }

  step('Esc 关闭弹窗')
  await page.keyboard.press('Escape')
  await wait(600)
  console.log(`  弹窗: ${(await page.$('[role=dialog]')) ? '仍在（Esc 未生效）' : '已关闭 ✅'}`)
  console.log(`  计划页是否提示需重扫: ${(await page.$('.banner.warn')) ? '是 ✅' : '否'}`)

  step('点击「预演（不写文件）」')
  if (!(await clickByText('预演'))) throw new Error('找不到「预演」按钮')
  await page.waitForFunction(() => document.querySelectorAll('.result .n').length > 0, { timeout: 240000 })
  await wait(1200)
  console.log('  预演结果:')
  ;(await page.$$eval('.result > div', (els) =>
    els.map((e) => `${e.querySelector('.n').textContent.trim()} ${e.querySelector('.l').textContent.trim()}`),
  )).forEach((r) => console.log(`    ${r}`))
  await page.screenshot({ path: '/tmp/ui-4-result.png', fullPage: true })
} catch (e) {
  console.log(`\n❌ 流程中断: ${e.message}`)
  await page.screenshot({ path: '/tmp/ui-fail.png', fullPage: true }).catch(() => {})
} finally {
  step('页面报错汇总')
  if (errors.length === 0) console.log('  ✅ 无 console / page / 网络错误')
  else errors.forEach((e) => console.log(`  ❌ ${e}`))
  await browser.close()
}
