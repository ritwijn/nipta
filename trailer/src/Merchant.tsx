import React from 'react'

// Lakshmi, as a painted figurine on a wooden base: saree, reading glasses, a ledger in her arms.
// `open` (0..1) is how loud she is this frame; her mouth, head and the ledger follow it. `frame` drives blinks and breathing.
export const Merchant: React.FC<{ open: number; frame: number; height: number; name?: boolean }> = ({ open, frame, height, name = true }) => {
  const blink = [0, 97, 211, 260, 380].some(b => (frame % 420) - b >= 0 && (frame % 420) - b < 5) ? 0.12 : 1
  const breath = Math.sin(frame / 22) * 1.6
  const tilt = Math.sin(frame / 37) * 1.5 + open * 2.5 * Math.sin(frame / 5)
  const mouth = 1.5 + open * 9
  return (
    <svg viewBox="0 0 400 560" height={height} style={{ overflow: 'visible', filter: 'drop-shadow(0 18px 18px rgba(60,40,20,.28))' }}>
      <defs>
        <linearGradient id="skin" x1="0" x2="1"><stop offset="0" stopColor="#b97a4b" /><stop offset="1" stopColor="#9c6136" /></linearGradient>
        <linearGradient id="saree" x1="0" x2="1" y1="0" y2="1"><stop offset="0" stopColor="#2f7454" /><stop offset="1" stopColor="#1e5139" /></linearGradient>
        <linearGradient id="wood" x1="0" x2="0" y1="0" y2="1"><stop offset="0" stopColor="#a8743f" /><stop offset="1" stopColor="#734621" /></linearGradient>
        <radialGradient id="sheen" cx="0.35" cy="0.3" r="0.8"><stop offset="0" stopColor="#fff" stopOpacity=".22" /><stop offset="1" stopColor="#fff" stopOpacity="0" /></radialGradient>
      </defs>

      {/* base */}
      <ellipse cx="200" cy="532" rx="150" ry="26" fill="#5c3818" />
      <rect x="50" y="500" width="300" height="32" fill="url(#wood)" />
      <ellipse cx="200" cy="500" rx="150" ry="26" fill="#b98451" />
      {name && <g><rect x="140" y="510" width="120" height="18" rx="2" fill="#d6b25e" stroke="#8a6a24" />
        <text x="200" y="523.5" textAnchor="middle" fontFamily="Manrope" fontWeight={800} fontSize="11" letterSpacing="2" fill="#4a3510">LAKSHMI</text></g>}

      <g transform={`translate(0 ${breath})`}>
        {/* saree body and pleats */}
        <path d="M120 300 C110 380 100 450 92 500 L308 500 C300 450 290 380 280 300 Z" fill="url(#saree)" />
        {[150, 175, 200, 225, 250].map(x => <path key={x} d={`M${x} 390 L${x - 6} 500`} stroke="#184630" strokeWidth="3" opacity=".6" />)}
        <path d="M92 500 L308 500 L306 484 L94 484 Z" fill="#d9a53a" />
        {/* blouse */}
        <path d="M128 240 Q200 222 272 240 L284 312 Q200 330 116 312 Z" fill="#8c2f39" />
        {/* pallu over the left shoulder */}
        <path d="M250 232 Q275 236 286 262 L210 420 Q180 410 168 392 Z" fill="#2f7454" />
        <path d="M250 232 Q275 236 286 262 L282 270 Q270 246 246 242 Z" fill="#d9a53a" />
        <path d="M168 392 Q180 410 210 420 L206 430 Q176 420 162 400 Z" fill="#d9a53a" />
        {/* arms holding the ledger */}
        <path d="M128 250 Q100 300 120 352 Q150 372 196 364 L196 344 Q160 346 146 334 Q140 300 150 262 Z" fill="url(#skin)" />
        <path d="M272 250 Q300 300 280 352 Q250 372 214 364 L214 344 Q244 346 256 334 Q262 300 250 262 Z" fill="url(#skin)" />
        <g transform={`rotate(${-4 + open * 2} 205 340)`}>
          <rect x="150" y="318" width="110" height="46" rx="3" fill="#b23a2e" />
          <rect x="154" y="322" width="102" height="38" rx="2" fill="none" stroke="#e0b65a" strokeWidth="1.5" />
          <text x="205" y="346" textAnchor="middle" fontFamily="Fraunces" fontSize="13" fill="#f3dfae">ಖಾತೆ</text>
        </g>
        <path d="M150 352 Q170 368 196 362" fill="url(#skin)" />
        {/* bangles */}
        {[0, 7].map(d => <ellipse key={d} cx={132 + d / 2} cy={336 + d} rx="11" ry="4" fill="none" stroke="#d9a53a" strokeWidth="3" />)}
        {[0, 7].map(d => <ellipse key={'r' + d} cx={268 - d / 2} cy={336 + d} rx="11" ry="4" fill="none" stroke="#d9a53a" strokeWidth="3" />)}
        {/* neck and chain */}
        <path d="M184 196 L184 236 Q200 248 216 236 L216 196 Z" fill="#9c6136" />
        <path d="M172 236 Q200 270 228 236" fill="none" stroke="#d9a53a" strokeWidth="2.5" />
        <circle cx="200" cy="256" r="4" fill="#d9a53a" />

        {/* head */}
        <g transform={`rotate(${tilt} 200 190)`}>
          <circle cx="200" cy="96" r="28" fill="#1d1510" />
          {[0, 1, 2, 3, 4, 5, 6].map(i => <circle key={i} cx={176 + i * 8} cy={84 + Math.abs(3 - i) * 3} r="4.5" fill="#fbf7ea" stroke="#e6dfc8" />)}
          <ellipse cx="200" cy="150" rx="46" ry="54" fill="url(#skin)" />
          <path d="M152 146 Q150 96 200 92 Q250 96 248 146 Q240 118 206 112 L200 104 L194 112 Q160 118 152 146 Z" fill="#1d1510" />
          <ellipse cx="200" cy="150" rx="46" ry="54" fill="url(#sheen)" />
          {/* ears and earrings */}
          <ellipse cx="154" cy="154" rx="6" ry="10" fill="#9c6136" />
          <ellipse cx="246" cy="154" rx="6" ry="10" fill="#9c6136" />
          <path d="M148 166 l6 0 l3 10 l-12 0 Z" fill="#d9a53a" /><path d="M243 166 l6 0 l3 10 l-12 0 Z" fill="#d9a53a" />
          {/* brows, eyes, glasses */}
          <path d="M170 132 Q181 126 191 131" stroke="#1d1510" strokeWidth="3" fill="none" strokeLinecap="round" />
          <path d="M209 131 Q219 126 230 132" stroke="#1d1510" strokeWidth="3" fill="none" strokeLinecap="round" />
          <ellipse cx="181" cy="145" rx="6" ry={5 * blink} fill="#20140c" />
          <ellipse cx="219" cy="145" rx="6" ry={5 * blink} fill="#20140c" />
          <circle cx="182.5" cy="143.5" r={1.4 * blink} fill="#fff" /><circle cx="220.5" cy="143.5" r={1.4 * blink} fill="#fff" />
          <rect x="166" y="136" width="30" height="20" rx="8" fill="none" stroke="#b8902f" strokeWidth="1.8" />
          <rect x="204" y="136" width="30" height="20" rx="8" fill="none" stroke="#b8902f" strokeWidth="1.8" />
          <path d="M196 144 Q200 141 204 144" stroke="#b8902f" strokeWidth="1.8" fill="none" />
          <circle cx="200" cy="122" r="3.6" fill="#c0262d" />
          {/* nose and mouth */}
          <path d="M200 152 Q197 164 201 168" stroke="#7d4a27" strokeWidth="2" fill="none" strokeLinecap="round" />
          <circle cx="207" cy="165" r="1.6" fill="#e9d38f" />
          <ellipse cx="200" cy={182 + mouth / 4} rx={9 + open * 2} ry={mouth} fill="#4a1a18" />
          <path d={`M189 ${181} Q200 ${177 - open} 211 181`} stroke="#7a2e2a" strokeWidth="3" fill="none" strokeLinecap="round" />
          <path d={`M190 ${183 + mouth * 1.4} Q200 ${188 + mouth * 1.6} 210 ${183 + mouth * 1.4}`} stroke="#7a2e2a" strokeWidth="3" fill="none" strokeLinecap="round" />
          <ellipse cx="172" cy="170" rx="8" ry="5" fill="#c4574a" opacity=".18" /><ellipse cx="228" cy="170" rx="8" ry="5" fill="#c4574a" opacity=".18" />
        </g>
      </g>
    </svg>
  )
}
