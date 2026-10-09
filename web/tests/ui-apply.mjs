// 从浏览器里真跑一次「执行复制」，验证全流程闭环。
//
// ⚠️ 这个脚本会**真的往 PHOTO_OUT 写文件**（不是预演）。请务必把 PHOTO_OUT
//    指向一个一次性的临时目录，别指向你要保留的归档目录。
//
// 用法：
//
//     PHOTO_SRC=~/Pictures/some_album \
//     PHOTO_OUT=/tmp/ui-apply-real \
//     node web/tests/ui-apply.mjs http://127.0.0.1:8787
//
// 源目录全程只读 —— 脚本不会碰它。跑完可以自己核对一遍：
//
//     find "$PHOTO_SRC" -type f -exec stat -f '%N %m %z' {} \; | sort > /tmp/a.txt
//     # 跑完之后
//     find "$PHOTO_SRC" -type f -exec stat -f '%N %m %z' {} \; | sort > /tmp/b.txt
//     diff /tmp/a.txt /tmp/b.txt && echo "✅ 源文件零改动"
import puppeteer from 'puppeteer-core'

const SRC = process.env.PHOTO_SRC
const OUT = process.env.PHOTO_OUT
const CHROME = process.env.CHROME_BIN ?? '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'

if (!SRC || !OUT) {
  console.error('需要 PHOTO_SRC 与 PHOTO_OUT 两个环境变量。')
  console.error('例：PHOTO_SRC=~/Pictures/some_album PHOTO_OUT=/tmp/apply-test node web/tests/ui-apply.mjs')
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
page.on('pageerror', (e) => errors.push(`pageerror: ${e.message}`))
page.on('console', (m) => m.type() === 'error' && errors.push(`console: ${m.text()}`))

const wait = (ms) => new Promise((r) => setTimeout(r, ms))
async function clickByText(text) {
  for (const b of await page.$$('button')) {
    if ((await b.evaluate((el) => el.textContent)).includes(text)) {
      await b.click()
      return true
    }
  }
  return false
}

await page.goto(`${BASE}/`, { waitUntil: 'networkidle0' })

const inputs = await page.$$('input[type=text]')
for (const [i, v] of [SRC, OUT].entries()) {
  await inputs[i].click()
  await inputs[i].evaluate((el) => el.select())   // 先全选再输入，否则是追加
  await inputs[i].type(v)
}

console.log('▸ 扫描')
await clickByText('开始扫描')
await page.waitForFunction(
  () => [...document.querySelectorAll('h2')].some((h) => h.textContent.includes('审阅计划')),
  { timeout: 120000 },
)
await wait(1200)

console.log('▸ 执行复制（真写盘）')
await clickByText('执行复制')
await page.waitForFunction(() => document.querySelectorAll('.result .n').length > 0, { timeout: 300000 })
await wait(1500)

const result = await page.$$eval('.result > div', (els) =>
  els.map((e) => `${e.querySelector('.n').textContent.trim()} ${e.querySelector('.l').textContent.trim()}`),
)
console.log('  结果:')
result.forEach((r) => console.log(`    ${r}`))
console.log(`  完成徽标: ${await page.$eval('.pill.ok', (e) => e.textContent.trim()).catch(() => '无')}`)
console.log(`  「源目录未改动」提示: ${(await page.$$eval('.after', (e) => e.map((x) => x.textContent.trim()))).join('') || '无'}`)

await page.screenshot({ path: '/tmp/ui-5-applied.png', fullPage: true })
console.log(errors.length ? `\n❌ 页面报错:\n  ${errors.join('\n  ')}` : '\n✅ 无页面报错')
await browser.close()
