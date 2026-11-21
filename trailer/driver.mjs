// Plays one Friday in Nipta while the merchant talks. Each line of lines.json gets a mark('line') where it starts, the clicks that
// go with it, and then waits until the line has been spoken (lengths from out/voice.json), so picture and voice stay together
// whatever the voice. mark('focus') tells the edit which part of the screen to move in on.
import fs from 'node:fs'
import path from 'node:path'

const voice = JSON.parse(fs.readFileSync(path.join(process.env.TRAILER_OUT, 'voice.json'), 'utf8'))
const GAP = 450 // ms of quiet between lines

export async function setup({ page }) {
  await page.evaluate(() => { sessionStorage.clear(); localStorage.clear() })
  await page.reload({ waitUntil: 'networkidle0' })
}

export async function play({ page, wait, now, mark, tap, fps }) {
  const frame = 1000 / fps
  const rect = sel => page.$eval(sel, el => { const r = el.getBoundingClientRect(); return { x: r.left, y: r.top, w: r.width, h: r.height } })
  // the part of the element that is on screen, with some room around it
  const focus = async (sel, pad = 16) => {
    const r = await rect(sel), x = Math.max(0, r.x - pad), y = Math.max(0, r.y - pad)
    await mark('focus', { x, y, w: Math.min(1280, r.x + r.w + pad) - x, h: Math.min(720, r.y + r.h + pad) - y })
  }
  const wide = () => mark('focus', { x: 0, y: 0, w: 1280, h: 720 })
  // eased scroll so the element's top sits `top` px below the viewport's top
  async function scrollTo(sel, top = 110, ms = 700) {
    const from = await page.evaluate(() => scrollY)
    const to = Math.max(0, from + (await rect(sel)).y - top)
    const n = Math.round(ms / frame)
    for (let i = 1; i <= n; i++) {
      const p = i / n, e = p < 0.5 ? 4 * p ** 3 : 1 - (-2 * p + 2) ** 3 / 2
      await page.evaluate(y => scrollTo(0, y), from + (to - from) * e)
      await wait(frame)
    }
  }
  // wait (in recorded time) for an element the app is about to draw
  const until = async sel => { for (let i = 0; i < 40 && !(await page.$(sel)); i++) await wait(100) }
  // press an element, scrolling it into view first if it is off screen
  const press = async (sel, top = 200) => {
    await until(sel)
    const r = await rect(sel)
    if (r.y < 60 || r.y + r.h > 700) await scrollTo(sel, top, 500)
    await tap(sel)
  }
  const type = async (sel, text) => { await press(sel); for (const ch of text) { await page.keyboard.type(ch); await wait(75) } }
  async function line(id, act) {
    const t = await mark('line', { id })
    await act()
    const left = t + voice[id].seconds * 1000 + GAP - (await now())
    if (left > 0) await wait(left)
  }

  await wait(400)
  await line('bring', async () => {
    await wide()
    await type('#pin', '2468')
    await press('#login button'); await wait(700)
    await focus('.col-bills .panel', 4)
    await press('#loadFixtures'); await wait(900)
    await press('#processBatch'); await wait(1600)
  })
  await line('today', async () => {
    await focus('#today', 10)
  })
  await line('sums', async () => {
    await press('#today [data-open="INV-B"]'); await wait(900)
    await focus('#detail .verdict'); await wait(2600)
    await scrollTo('#detail .checks', 140); await focus('#detail .checks .check.hold'); await wait(2200)
    await scrollTo('#detail table', 200); await focus('#detail .table-wrap')
  })
  await line('bank', async () => {
    await page.evaluate(() => scrollTo(0, 0))
    await press('#billList .bill[data-id="INV-D"]'); await wait(900)
    await focus('#detail .grid2'); await wait(3500)
    await scrollTo('#vendorCall', 380); await focus('#detail .finding:has(#vendorCall)', 10)
  })
  await line('twice', async () => {
    await page.evaluate(() => scrollTo(0, 0))
    await scrollTo('#billList .bill[data-id="INV-C"]', 380, 500)
    await press('#billList .bill[data-id="INV-C"]'); await wait(900)
    await page.evaluate(() => scrollTo(0, 0)); await wait(300)
    await focus('#detail .verdict', 12)
  })
  await line('ask', async () => {
    await wide()
    await page.select('#lang', 'kn'); await wait(500)
    await focus('.col-side .panel', 8)
    await type('#agentText', 'ಯಾವ ಬಿಲ್‌ಗಳು ಸರಿಯಾಗಿವೆ?')
    await press('#agentForm button[type=submit]'); await wait(1200)
    await focus('#agentReply', 10)
  })
  await line('card', async () => {
    await page.select('#lang', 'en'); await wait(400)
    await wide()
    await press('#billList .bill[data-id="INV-E"]'); await wait(900)
    await press('#propose'); await wait(900)
    await scrollTo('#approval', 120); await focus('#approval', 8)
  })
  await line('pay', async () => {
    await press('#approveBtn'); await wait(1400)
    await focus('#approval', 8)
    const b = await rect('#executeBtn')
    await press('#executeBtn'); await wait(250)
    // the second press lands where the button was; nothing more is sent
    await mark('press', { x: b.x + b.w / 2, y: b.y + b.h / 2 }); await page.mouse.down(); await wait(90); await page.mouse.up()
    await mark('release', { x: b.x + b.w / 2, y: b.y + b.h / 2 })
    await wait(1800)
    await scrollTo('#approval', 100); await focus('#approval', 8)
  })
  await line('log', async () => {
    await scrollTo('#activity', 90, 900)
    await focus('.col-side .panel:has(#activity)', 8)
  })
  await wide()
  await wait(600)
}
