import React from 'react'
import { Composition, continueRender, delayRender, staticFile } from 'remotion'
import { Data, FPS, schedule, Trailer } from './Trailer'

// Fonts the edit uses (Manrope and JetBrains Mono match the app); the render waits for them.
const fonts = document.createElement('link')
fonts.rel = 'stylesheet'
fonts.href = 'https://fonts.googleapis.com/css2?family=Fraunces:opsz,wght@9..144,600&family=Kalam&family=JetBrains+Mono:wght@600&family=Manrope:wght@600;700;800&family=Noto+Sans+Kannada:wght@600&display=block'
document.head.appendChild(fonts)
const fontWait = delayRender('fonts')
fonts.onload = () => document.fonts.ready.then(() => continueRender(fontWait))

const load = async (): Promise<Data> => ({
  timeline: await (await fetch(staticFile('timeline.json'))).json(),
  voice: await (await fetch(staticFile('voice.json'))).json(),
})
const meta = async ({ props }: { props: any }) => {
  const data = await load()
  return { durationInFrames: schedule(data).end, props: { ...props, data } }
}

export const Root: React.FC = () => <>
  <Composition id="Wide" component={Trailer as any} width={1920} height={1080} fps={FPS} durationInFrames={3600} defaultProps={{ layout: 'wide' }} calculateMetadata={meta} />
  <Composition id="Tall" component={Trailer as any} width={1080} height={1920} fps={FPS} durationInFrames={3600} defaultProps={{ layout: 'tall' }} calculateMetadata={meta} />
</>
