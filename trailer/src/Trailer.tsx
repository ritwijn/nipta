import React from 'react'
import { AbsoluteFill, Audio, Easing, interpolate, OffthreadVideo, Sequence, staticFile, useCurrentFrame } from 'remotion'
import script from '../lines.json'
import { Merchant } from './Merchant'

export const FPS = 30
const C = { paper: '#f3ead6', paper2: '#e9dcc0', ink: '#2a2418', mute: '#7a6c55', green: '#1f7a55', red: '#b8432f', amber: '#c98a1a', wood: '#8a5a32' }
const SANS = 'Manrope, sans-serif', SERIF = 'Fraunces, Georgia, serif'

type Line = { id: string; part: 'intro' | 'app' | 'outro'; text: string; step?: number; say?: string }
type Mark = { t: number; type: string; [k: string]: any }
export type Data = { timeline: { seconds: number; timeline: Mark[] }; voice: Record<string, { seconds: number; env: number[] }> }
const lines = script.lines as Line[]

// When each line is spoken (in frames), and where the intro, the app and the outro start.
export function schedule({ timeline, voice }: Data) {
  const f = (s: number) => Math.round(s * FPS)
  const said: { id: string; from: number; len: number; line: Line }[] = []
  let t = f(0.9)
  for (const l of lines.filter(l => l.part === 'intro')) { said.push({ id: l.id, from: t, len: f(voice[l.id].seconds), line: l }); t += f(voice[l.id].seconds + 0.55) }
  const app = t + f(0.4)
  for (const m of timeline.timeline.filter(m => m.type === 'line')) {
    const l = lines.find(l => l.id === m.id)!
    said.push({ id: l.id, from: app + f(m.t / 1000), len: f(voice[l.id].seconds), line: l })
  }
  const outro = app + f(timeline.seconds)
  const close = lines.find(l => l.part === 'outro')!
  said.push({ id: close.id, from: outro + f(1.1), len: f(voice[close.id].seconds), line: close })
  const end = outro + f(1.1 + voice[close.id].seconds + 3.2)
  return { said, app, outro, end }
}

const ease = Easing.bezier(0.45, 0, 0.2, 1)

// Where the camera looks on the recorded page (CSS px of the 1280x720 page), eased between the driver's focus marks.
function camera(marks: Mark[], t: number, BH: number, maxZoom: number) {
  // BH: the box's height in page px (its width is always the page's 1280); the camera never shows past the page's edges
  const W = 1280, H = 720, min = Math.max(1, BH / H)
  const cam = (m?: Mark) => {
    const s = m ? Math.max(min, Math.min(maxZoom, Math.min(W / m.w, BH / m.h) * 0.92)) : min
    const vw = W / s, vh = BH / s, x = m ? m.x + m.w / 2 : W / 2, y = m ? m.y + m.h / 2 : H / 2
    return { s, x: Math.min(W - vw / 2, Math.max(vw / 2, x)), y: Math.min(H - vh / 2, Math.max(vh / 2, y)) }
  }
  const focus = marks.filter(m => m.type === 'focus')
  const i = focus.findLastIndex(m => m.t <= t)
  if (i < 0) return cam()
  const a = cam(focus[i - 1]), b = cam(focus[i]), p = ease(Math.min(1, (t - focus[i].t) / 900))
  return { s: a.s + (b.s - a.s) * p, x: a.x + (b.x - a.x) * p, y: a.y + (b.y - a.y) * p }
}

// The recorded app with its camera, a mouse pointer and click ripples, drawn into a box w x h.
const Screen: React.FC<{ data: Data; w: number; h: number; maxZoom: number }> = ({ data, w, h, maxZoom }) => {
  const frame = useCurrentFrame(), t = (frame / FPS) * 1000
  const marks = data.timeline.timeline
  const k = w / 1280
  const cam = camera(marks, t, h / k, maxZoom)
  const fingers = marks.filter(m => m.type === 'finger' || m.type === 'press' || m.type === 'release')
  const at = fingers.findLast(m => m.t <= t) ?? { x: 640, y: 680 }
  const presses = marks.filter(m => m.type === 'press' && m.t <= t && t - m.t < 450)
  return (
    <div style={{ width: w, height: h, overflow: 'hidden', position: 'relative', borderRadius: 14, background: '#efe5cc' }}>
      <div style={{ position: 'absolute', width: 1280, height: 720, transformOrigin: '0 0',
        transform: `scale(${k}) scale(${cam.s}) translate(${640 / cam.s - cam.x}px, ${(h / k / 2) / cam.s - cam.y}px)` }}>
        <OffthreadVideo src={staticFile('gameplay-av.mp4')} muted style={{ width: 1280, height: 720 }} />
        {presses.map(p => {
          const q = (t - p.t) / 450
          return <div key={p.t} style={{ position: 'absolute', left: p.x - 26, top: p.y - 26, width: 52, height: 52, borderRadius: 99,
            border: `3px solid ${C.green}`, opacity: 1 - q, transform: `scale(${0.4 + q})` }} />
        })}
        <svg width="26" height="30" viewBox="0 0 26 30" style={{ position: 'absolute', left: at.x - 3, top: at.y - 2, filter: 'drop-shadow(0 2px 2px rgba(0,0,0,.35))' }}>
          <path d="M3 2 L3 24 L9 18.5 L13 27 L17 25.2 L13 16.8 L21 16.8 Z" fill="#fff" stroke="#1a1a1a" strokeWidth="1.6" strokeLinejoin="round" />
        </svg>
      </div>
    </div>
  )
}

// How loud she is right now (0..1), for her mouth.
function loudness(data: Data, said: ReturnType<typeof schedule>['said'], frame: number) {
  const s = said.find(s => frame >= s.from && frame < s.from + s.len)
  return s ? data.voice[s.id].env[frame - s.from] ?? 0 : 0
}

// The sentence being spoken: a line is split into sentences, each shown for its share of the line by length.
function subtitle(said: ReturnType<typeof schedule>['said'], frame: number) {
  const s = said.find(s => frame >= s.from - 3 && frame < s.from + s.len + 8)
  if (!s) return null
  const parts = s.line.text.match(/[^.?!]+[.?!]+/g) ?? [s.line.text]
  const total = parts.reduce((n, p) => n + p.length, 0)
  let acc = 0
  for (const p of parts) { acc += p.length; if (frame - s.from <= (acc / total) * s.len) return p.trim() }
  return parts[parts.length - 1].trim()
}

const Subtitle: React.FC<{ text: string | null; size: number; width: number }> = ({ text, size, width }) => text ? (
  <div style={{ maxWidth: width, margin: '0 auto', textAlign: 'center', fontFamily: SANS, fontWeight: 700, fontSize: size, lineHeight: 1.3, color: C.ink,
    background: 'rgba(255,250,238,.92)', padding: `${size * 0.35}px ${size * 0.7}px`, borderRadius: 10, boxShadow: '0 6px 18px rgba(80,60,30,.12)' }}>{text}</div>
) : null

// The six steps of a Friday, with the current one filled in.
const Steps: React.FC<{ current: number; size: number; vertical?: boolean }> = ({ current, size, vertical }) => (
  <div style={{ display: 'flex', flexDirection: vertical ? 'column' : 'row', gap: size * 0.5, flexWrap: 'wrap', justifyContent: 'center' }}>
    {script.steps.map((s, i) => {
      const on = i === current, done = i < current
      return <div key={s} style={{ display: 'flex', alignItems: 'center', gap: size * 0.4, fontFamily: SANS, fontWeight: 700, fontSize: size,
        color: on ? '#fff' : done ? C.green : C.mute, background: on ? C.green : 'rgba(255,250,238,.7)', border: `2px solid ${on || done ? C.green : '#d8ccb0'}`,
        padding: `${size * 0.3}px ${size * 0.7}px`, borderRadius: 99 }}>
        <span style={{ fontVariantNumeric: 'tabular-nums', opacity: on ? 0.85 : 1 }}>{done ? '✓' : i + 1}</span>{s}
      </div>
    })}
  </div>
)

// The shop behind her: shelves of hardware, a counter, this week's bills.
const Shop: React.FC<{ w: number; h: number }> = ({ w, h }) => (
  <svg width={w} height={h} viewBox="0 0 1920 1080" preserveAspectRatio="xMidYMax slice" style={{ position: 'absolute', inset: 0 }}>
    <rect width="1920" height="1080" fill={C.paper} />
    {[180, 380, 580].map(y => <g key={y}>
      <rect x="0" y={y + 150} width="1920" height="16" fill="#9a6a3b" />
      {Array.from({ length: 16 }, (_, i) => {
        const x = 40 + i * 118, bh = 70 + ((i * 37 + y) % 60), col = ['#c9a46a', '#7d9b8a', '#b5683f', '#d8c08d', '#8a7f6a'][(i + y / 20) % 5]
        return <g key={i}><rect x={x} y={y + 150 - bh} width="96" height={bh} rx="3" fill={col} opacity=".55" />
          <rect x={x + 20} y={y + 150 - bh + 14} width="56" height="16" fill="#fff" opacity=".45" /></g>
      })}
    </g>)}
    <rect x="0" y="830" width="1920" height="250" fill="#8a5a32" />
    <rect x="0" y="830" width="1920" height="22" fill="#a8743f" />
  </svg>
)

const Bills: React.FC<{ x: number; y: number; scale: number }> = ({ x, y, scale }) => (
  <svg width={360 * scale} height={200 * scale} viewBox="0 0 360 200" style={{ position: 'absolute', left: x, top: y }}>
    {[[-8, 0, 'கவேரி'], [5, 30, 'कावेरी'], [-2, 60, 'INVOICE']].map(([r, dx, word], i) => (
      <g key={i} transform={`translate(${40 + (dx as number)} ${20 + i * 14}) rotate(${r as number})`}>
        <rect width="220" height="150" fill="#fffdf6" stroke="#d6cbb0" />
        <text x="16" y="30" fontFamily="Kalam, cursive" fontSize="20" fill="#3b4a7a">{word}</text>
        {[50, 70, 90, 110].map(ly => <line key={ly} x1="16" x2={150 + ((ly * 7) % 50)} y1={ly} y2={ly} stroke="#a7b0c8" strokeWidth="2" />)}
        <text x="150" y="136" fontFamily="Kalam, cursive" fontSize="18" fill="#3b4a7a">₹{[8200, 6000, 4500][i]}</text>
      </g>
    ))}
  </svg>
)

const Title: React.FC<{ children: React.ReactNode; size: number; at: number; style?: React.CSSProperties }> = ({ children, size, at, style }) => {
  const frame = useCurrentFrame()
  const o = interpolate(frame - at, [0, 14], [0, 1], { extrapolateLeft: 'clamp', extrapolateRight: 'clamp' })
  return <div style={{ fontFamily: SERIF, fontWeight: 600, fontSize: size, lineHeight: 1.08, color: C.ink, opacity: o, transform: `translateY(${(1 - o) * 14}px)`, ...style }}>{children}</div>
}

export const Trailer: React.FC<{ data: Data; layout: 'wide' | 'tall' }> = ({ data, layout }) => {
  const frame = useCurrentFrame()
  const { said, app, outro, end } = schedule(data)
  const tall = layout === 'tall'
  const W = tall ? 1080 : 1920, H = tall ? 1920 : 1080
  const open = loudness(data, said, frame)
  const now = said.filter(s => s.from <= frame).at(-1)
  const step = now?.line.part === 'app' ? now.line.step ?? 0 : -1
  const speaking = (f: number) => said.some(s => f >= s.from - 6 && f < s.from + s.len + 6)
  const fade = (a: number, b: number) => interpolate(frame, [a, a + 12, b - 12, b], [0, 1, 1, 0], { extrapolateLeft: 'clamp', extrapolateRight: 'clamp' })
  const sub = subtitle(said, frame)

  return (
    <AbsoluteFill style={{ background: C.paper }}>
      {/* sound: her lines, and the music under them, lower while she talks */}
      {said.map(s => <Sequence key={s.id} from={s.from} durationInFrames={s.len + 2}><Audio src={staticFile(`voice/${s.id}.wav`)} /></Sequence>)}
      <Audio src={staticFile('music.wav')} volume={f => (speaking(f) ? 0.16 : 0.34) * interpolate(f, [0, 20, end - 60, end], [0, 1, 1, 0], { extrapolateRight: 'clamp' })} />

      {/* intro: at the shop counter */}
      {frame < app + 12 && <AbsoluteFill style={{ opacity: fade(-12, app + 12) }}>
        <Shop w={W} h={H} />
        <div style={{ position: 'absolute', left: tall ? 50 : 790, top: tall ? 130 : 150, width: tall ? 980 : 1060, padding: '40px 44px', background: 'rgba(250,244,230,.94)', borderRadius: 18, boxShadow: '0 20px 50px rgba(80,60,30,.15)' }}>
          <Title size={tall ? 66 : 80} at={said[0].from}>Every Friday,<br />a pile of bills.</Title>
          <Title size={tall ? 36 : 40} at={said[1].from} style={{ fontFamily: SANS, fontWeight: 600, color: C.mute, marginTop: 28 }}>Tamil, Hindi, English. Printed and handwritten.</Title>
          <Title size={tall ? 36 : 40} at={said[1].from + 150} style={{ fontFamily: SANS, fontWeight: 700, color: C.red, marginTop: 14 }}>One had a bank account that wasn't the supplier's.</Title>
        </div>
        <Bills x={tall ? 560 : 1420} y={tall ? 1060 : 560} scale={tall ? 1.2 : 1} />
        <div style={{ position: 'absolute', left: tall ? 120 : 250, bottom: tall ? 520 : 70 }}><Merchant open={open} frame={frame} height={tall ? 820 : 760} /></div>
      </AbsoluteFill>}

      {/* the app: recorded screen, steps on top, Lakshmi beside it */}
      {frame >= app - 12 && frame < outro + 12 && <AbsoluteFill style={{ opacity: fade(app - 12, outro + 12), background: `linear-gradient(${C.paper}, ${C.paper2})` }}>
        <Sequence from={app}>
          {tall ? <>
            <div style={{ position: 'absolute', top: 70, width: W }}><Steps current={step} size={26} /></div>
            <div style={{ position: 'absolute', top: 300, left: 40, boxShadow: '0 24px 60px rgba(60,40,20,.25)', borderRadius: 14 }}><Screen data={data} w={1000} h={760} maxZoom={1.9} /></div>
            {now?.line.say && <div style={{ position: 'absolute', top: 210, width: W, textAlign: 'center', fontFamily: SERIF, fontWeight: 600, fontSize: 52, color: C.ink }}>{now.line.say}</div>}
          </> : <>
            <div style={{ position: 'absolute', top: 34, left: 470, width: 1400 }}><Steps current={step} size={22} /></div>
            {now?.line.say && <div style={{ position: 'absolute', top: 108, left: 470, width: 1400, fontFamily: SERIF, fontWeight: 600, fontSize: 46, color: C.ink }}>{now.line.say}</div>}
            <div style={{ position: 'absolute', top: 180, left: 470, boxShadow: '0 24px 60px rgba(60,40,20,.25)', borderRadius: 14 }}><Screen data={data} w={1400} h={740} maxZoom={1.7} /></div>
          </>}
        </Sequence>
        <div style={{ position: 'absolute', left: tall ? 60 : 30, bottom: tall ? 300 : 40 }}><Merchant open={open} frame={frame} height={tall ? 560 : 520} /></div>
      </AbsoluteFill>}

      {/* outro */}
      {frame >= outro - 12 && <AbsoluteFill style={{ opacity: fade(outro - 12, end + 30) }}>
        <Shop w={W} h={H} />
        <div style={{ position: 'absolute', left: tall ? 50 : 790, top: tall ? 140 : 170, width: tall ? 980 : 1060, padding: '40px 44px', background: 'rgba(250,244,230,.94)', borderRadius: 18, boxShadow: '0 20px 50px rgba(80,60,30,.15)' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 22 }}>
            <div style={{ width: 92, height: 92, borderRadius: 22, background: C.green, color: '#fff', fontFamily: SANS, fontWeight: 800, fontSize: 56, display: 'grid', placeItems: 'center' }}>N</div>
            <Title size={110} at={outro}>Nipta</Title>
          </div>
          <Title size={tall ? 52 : 56} at={outro + 20} style={{ marginTop: 26 }}>Pay the right bill,<br />to the right person.</Title>
          <Title size={30} at={outro + 50} style={{ fontFamily: SANS, fontWeight: 600, color: C.mute, marginTop: 30 }}>Reads bills with Sarvam AI · pays through Cashfree Payouts</Title>
          <Title size={34} at={outro + 70} style={{ fontFamily: 'JetBrains Mono, monospace', fontWeight: 600, color: C.green, marginTop: 18 }}>github.com/ritwijn/nipta</Title>
        </div>
        <div style={{ position: 'absolute', left: tall ? 120 : 250, bottom: tall ? 520 : 70 }}><Merchant open={open} frame={frame} height={tall ? 820 : 760} /></div>
      </AbsoluteFill>}

      <div style={{ position: 'absolute', left: tall ? 40 : 470, right: tall ? 40 : 50, bottom: tall ? 120 : 26, display: frame >= app && frame < outro ? 'block' : 'none' }}>
        <Subtitle text={sub} size={tall ? 40 : 30} width={tall ? 1000 : 1300} />
      </div>
      <div style={{ position: 'absolute', left: tall ? 40 : 820, right: tall ? 40 : 80, bottom: tall ? 300 : 120, display: frame < app || frame >= outro ? 'block' : 'none' }}>
        <Subtitle text={sub} size={tall ? 40 : 34} width={tall ? 1000 : 1000} />
      </div>
    </AbsoluteFill>
  )
}
