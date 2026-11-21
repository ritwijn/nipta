// A quiet bed under the voice: a tanpura-like drone on Sa and Pa, and a slow plucked figure in a major pentatonic, 80 BPM.
// Writes out/music.wav (48 kHz stereo, 130 s). Kept low; the edit ducks it further while she speaks.
import fs from 'node:fs'
import path from 'node:path'

const OUT = path.join(path.dirname(new URL(import.meta.url).pathname), 'out')
const SR = 48000, LEN = 130, N = SR * LEN
const L = new Float32Array(N), R = new Float32Array(N)
const hz = semi => 146.83 * 2 ** (semi / 12) // Sa = D3

// drone: four strings plucked in turn, each a decaying stack of harmonics with a slight buzz (jivari)
const strings = [7 - 12, 0, 0, -12] // Pa (low), Sa, Sa, Sa (low)
for (let t = 0, k = 0; t < LEN; t += 1.5, k++) {
  const f = hz(strings[k % 4]), start = Math.floor(t * SR), dur = 5 * SR, pan = 0.35 + 0.3 * (k % 2)
  for (let i = 0; i < dur && start + i < N; i++) {
    const s = i / SR, env = Math.exp(-s * 0.9) * Math.min(1, s * 40)
    let v = 0
    for (let h = 1; h <= 8; h++) v += Math.sin(2 * Math.PI * f * h * s + h * 0.3) / (h ** 1.1) * (1 + 0.25 * Math.sin(2 * Math.PI * 0.7 * s * h))
    v *= env * 0.045
    L[start + i] += v * (1 - pan); R[start + i] += v * pan
  }
}

// plucks: Karplus-Strong, a phrase of 8 beats that repeats with small changes
const pent = [0, 2, 4, 7, 9, 12, 14]
const phrase = [[0, 4], [1, 2], [2, 3], [4, 4], [3, 2], [2, 3], [1, 2], [0, 5]]
const beat = 60 / 80
function pluck(t, f, gain, pan) {
  const p = Math.round(SR / f), buf = new Float32Array(p)
  let seed = Math.floor(f * 1000 + t * 7)
  for (let i = 0; i < p; i++) { seed = (seed * 16807) % 2147483647; buf[i] = (seed / 2147483647) * 2 - 1 }
  const start = Math.floor(t * SR)
  for (let i = 0, j = 0; i < SR * 3 && start + i < N; i++, j = (j + 1) % p) {
    const v = buf[j]; buf[j] = 0.4985 * (v + buf[(j + 1) % p])
    const s = v * gain * Math.min(1, i / 60)
    L[start + i] += s * (1 - pan); R[start + i] += s * pan
  }
}
for (let bar = 0, t = 4 * beat; t < LEN - 6; bar++) {
  for (const [deg, octUp] of phrase) {
    const d = (deg + (bar % 3 === 2 ? 1 : 0)) % pent.length
    pluck(t, hz(pent[d] + 12), 0.09, 0.3 + 0.4 * ((d + bar) % 2))
    if (octUp === 5 && bar % 2) pluck(t + beat / 2, hz(pent[(d + 2) % pent.length] + 12), 0.05, 0.6)
    t += beat
  }
}

// fade out the last 4 s, then 16-bit stereo WAV
const pcm = Buffer.alloc(N * 4)
for (let i = 0; i < N; i++) {
  const fade = Math.min(1, (N - i) / (4 * SR))
  pcm.writeInt16LE(Math.round(Math.max(-1, Math.min(1, Math.tanh(L[i] * 1.2) * fade)) * 32767), i * 4)
  pcm.writeInt16LE(Math.round(Math.max(-1, Math.min(1, Math.tanh(R[i] * 1.2) * fade)) * 32767), i * 4 + 2)
}
const h = Buffer.alloc(44)
h.write('RIFF', 0); h.writeUInt32LE(36 + pcm.length, 4); h.write('WAVE', 8); h.write('fmt ', 12); h.writeUInt32LE(16, 16)
h.writeUInt16LE(1, 20); h.writeUInt16LE(2, 22); h.writeUInt32LE(SR, 24); h.writeUInt32LE(SR * 4, 28); h.writeUInt16LE(4, 32); h.writeUInt16LE(16, 34)
h.write('data', 36); h.writeUInt32LE(pcm.length, 40)
fs.mkdirSync(OUT, { recursive: true })
fs.writeFileSync(path.join(OUT, 'music.wav'), Buffer.concat([h, pcm]))
console.log('out/music.wav')
