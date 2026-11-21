// Speaks every line of lines.json with Sarvam Bulbul (an Indian woman's voice) into out/voice/<id>.wav, and writes out/voice.json:
// each line's length and a loudness value per video frame (the merchant's mouth follows it). Lines already spoken are kept, so
// changing one line only re-records that line. node voice.mjs [--force] [--speaker name]
import fs from 'node:fs'
import path from 'node:path'

const HERE = path.dirname(new URL(import.meta.url).pathname)
const OUT = path.join(HERE, 'out')
const FPS = 30
const args = process.argv.slice(2)
const script = JSON.parse(fs.readFileSync(path.join(HERE, 'lines.json'), 'utf8'))
const speaker = args.includes('--speaker') ? args[args.indexOf('--speaker') + 1] : script.speaker
const force = args.includes('--force')

const env = Object.fromEntries(fs.readFileSync(path.join(HERE, '../.env'), 'utf8').split('\n')
  .map(l => l.match(/^\s*([A-Z0-9_]+)\s*=\s*([^#\n]*)/)).filter(Boolean).map(m => [m[1], m[2].trim()]))
const key = process.env.SARVAM_API_KEY || env.SARVAM_API_KEY
if (!key) { console.error('No SARVAM_API_KEY in ../.env'); process.exit(1) }

// 16-bit PCM WAV -> mono samples in -1..1
function readWav(buf) {
  const rate = buf.readUInt32LE(24), channels = buf.readUInt16LE(22)
  let o = 12
  while (buf.toString('ascii', o, o + 4) !== 'data') o += 8 + buf.readUInt32LE(o + 4)
  const n = buf.readUInt32LE(o + 4) / 2 / channels, s = new Float32Array(n)
  for (let i = 0; i < n; i++) s[i] = buf.readInt16LE(o + 8 + i * 2 * channels) / 32768
  return { rate, s }
}

fs.mkdirSync(path.join(OUT, 'voice'), { recursive: true })
const meta = {}
for (const line of script.lines) {
  const file = path.join(OUT, 'voice', `${line.id}.wav`)
  const stamp = JSON.stringify([line.text, speaker, script.pace])
  const stampFile = file + '.said'
  if (force || !fs.existsSync(file) || fs.readFileSync(stampFile, 'utf8') !== stamp) {
    const res = await fetch('https://api.sarvam.ai/text-to-speech', {
      method: 'POST',
      headers: { 'api-subscription-key': key, 'Content-Type': 'application/json' },
      body: JSON.stringify({ text: line.text, target_language_code: 'en-IN', model: 'bulbul:v3', speaker, pace: script.pace, speech_sample_rate: 24000 }),
    })
    if (!res.ok) { console.error(`${line.id}: HTTP ${res.status} ${await res.text()}`); process.exit(1) }
    fs.writeFileSync(file, Buffer.from((await res.json()).audios[0], 'base64'))
    fs.writeFileSync(stampFile, stamp)
    console.log(`spoke ${line.id}`)
  }
  const { rate, s } = readWav(fs.readFileSync(file))
  const per = rate / FPS, env = []
  for (let f = 0; f * per < s.length; f++) {
    let sum = 0, n = 0
    for (let i = Math.floor(f * per); i < Math.min(s.length, (f + 1) * per); i++) { sum += s[i] * s[i]; n++ }
    env.push(Math.round(Math.min(1, Math.sqrt(sum / n) * 6) * 100) / 100)
  }
  meta[line.id] = { seconds: s.length / rate, env }
}
fs.writeFileSync(path.join(OUT, 'voice.json'), JSON.stringify(meta))
const total = Object.values(meta).reduce((t, m) => t + m.seconds, 0)
console.log(`${script.lines.length} lines, ${total.toFixed(1)}s of speech, voice ${speaker}`)
