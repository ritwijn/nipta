"""Build the README figures (light + dark) in the app's "Shop counter" look.

    python docs/img/build_figures.py

Plain animated SVG (CSS keyframes + SMIL), no dependencies. Animations stop under prefers-reduced-motion
and settle on the final state. Numbers and names come from the demo fixtures, not invented.
"""
from __future__ import annotations

from pathlib import Path
from xml.sax.saxutils import escape

OUT = Path(__file__).resolve().parent
T = 12  # seconds per loop

THEMES = {
    "light": dict(bg="#F1E6C8", card1="#F9F1DC", card2="#E6D7B0", sunk1="#E2D2A9", sunk2="#F4EBD2",
                  ink="#2A2217", ink2="#4A3F2E", muted="#6A5C44", faint="#B3A47E", line="#D9C9A0",
                  hl="#FFFCF0", hl_o=0.95, sh="#8A6C38", sh_o=0.34,
                  green="#3CCB8E", green_ink="#1E7656", amber="#EDA62F", amber_ink="#8A5A08",
                  red="#F0644A", red_ink="#A8361F", blue="#34449F", cap="#1E7656", cap_deep="#145A40"),
    "dark": dict(bg="#17140F", card1="#2C261D", card2="#221D16", sunk1="#1A1611", sunk2="#28221A",
                 ink="#F4EBD2", ink2="#E2D2A9", muted="#B3A47E", faint="#5E533F", line="#3A3228",
                 hl="#FFF0D2", hl_o=0.05, sh="#000000", sh_o=0.6,
                 green="#3CCB8E", green_ink="#5BE0A6", amber="#EDA62F", amber_ink="#F2B84E",
                 red="#F0644A", red_ink="#FF8A72", blue="#8E9BE8", cap="#1E7656", cap_deep="#0E3F2D"),
}

SANS = "Manrope, 'Segoe UI', 'Helvetica Neue', Arial, 'Noto Sans Kannada', 'Noto Sans Devanagari', sans-serif"
LABEL = "'Chakra Petch', 'Segoe UI', Arial, sans-serif"
MONO = "'JetBrains Mono', ui-monospace, Menlo, Consolas, monospace"


def e(s: str) -> str:
    return escape(s, {'"': "&quot;"})


def appear(name: str, start: float, end: float = 95, dx: int = 0, dy: int = 0) -> str:
    """Keyframes: hidden until start%, fades in, visible until end%, then gone quickly (loop restarts at 100%)."""
    move = f" transform: translate({dx}px, {dy}px);" if dx or dy else ""
    still = " transform: none;" if dx or dy else ""
    on = min(start + 3, end - 0.5)
    off = min(end + 2, 100)
    return (f"@keyframes {name} {{ 0%, {start:.1f}% {{ opacity: 0;{move} }} {on:.1f}%, {end:.1f}% {{ opacity: 1;{still} }} "
            f"{off:.1f}%, 100% {{ opacity: 0; }} }}\n"
            f".{name} {{ animation: {name} {T}s ease-out infinite; }}\n")


def head(w: int, h: int, t: dict, title: str, desc: str, css: str) -> str:
    return f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" width="{w}" height="{h}" role="img" aria-labelledby="ttl dsc">
<title id="ttl">{e(title)}</title>
<desc id="dsc">{e(desc)}</desc>
<defs>
  <linearGradient id="card" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="{t['card1']}"/><stop offset="1" stop-color="{t['card2']}"/></linearGradient>
  <linearGradient id="sunk" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="{t['sunk1']}"/><stop offset="1" stop-color="{t['sunk2']}"/></linearGradient>
  <linearGradient id="cap" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#2A8C66"/><stop offset="1" stop-color="{t['cap']}"/></linearGradient>
  <filter id="raise" x="-10%" y="-20%" width="120%" height="150%">
    <feDropShadow dx="-4" dy="-4" stdDeviation="5" flood-color="{t['hl']}" flood-opacity="{t['hl_o']}"/>
    <feDropShadow dx="5" dy="6" stdDeviation="7" flood-color="{t['sh']}" flood-opacity="{t['sh_o']}"/>
  </filter>
  <filter id="key" x="-10%" y="-30%" width="120%" height="170%">
    <feDropShadow dx="-2" dy="-2" stdDeviation="3" flood-color="{t['hl']}" flood-opacity="{t['hl_o']}"/>
    <feDropShadow dx="3" dy="3" stdDeviation="4" flood-color="{t['sh']}" flood-opacity="{t['sh_o'] * 0.8:.2f}"/>
  </filter>
  <filter id="glow" x="-100%" y="-100%" width="300%" height="300%"><feGaussianBlur stdDeviation="2.4"/></filter>
</defs>
<style>
text {{ font-family: {SANS}; fill: {t['ink']}; }}
.lbl {{ font-family: {LABEL}; font-weight: 700; letter-spacing: 0.14em; font-size: 10.5px; fill: {t['ink2']}; }}
.mono {{ font-family: {MONO}; }}
.muted {{ fill: {t['muted']}; }}
{css}
@media (prefers-reduced-motion: reduce) {{
  * {{ animation: none !important; }}
  .a {{ opacity: 1 !important; transform: none !important; }}
  .gone {{ opacity: 0 !important; }}
}}
</style>
<rect width="{w}" height="{h}" rx="28" fill="{t['bg']}"/>
"""


def chip(x, y, text, color, ink, t, cls="", w=None):
    w = w or (len(text) * 7.1 + 30)
    return (f'<g class="{cls}"><rect x="{x}" y="{y}" width="{w:.0f}" height="22" rx="11" fill="url(#sunk)"/>'
            f'<circle cx="{x + 12}" cy="{y + 11}" r="3.5" fill="{color}"/>'
            f'<text x="{x + 21}" y="{y + 15}" class="lbl" style="fill:{ink};font-size:10px;letter-spacing:.08em">{e(text)}</text></g>')


# ---------------------------------------------------------------- hero

def hero(t: dict) -> str:
    w, h = 880, 430
    css = ""
    bills = [  # (initials, vendor, ref, lang, amount, failing check index, verdict, color key)
        ("CP", "Coimbatore Packing", "CP-201", "ಕನ್ನಡ", "₹3,200", None, "SAFE TO PAY", "green"),
        ("KF", "Kaveri Fasteners", "KF/182-A", "हिन्दी", "₹8,200", 2, "CHECK · LOOKS LIKE A COPY", "amber"),
        ("EH", "Eastern Hardware", "EH-040", "English", "₹6,000", 4, "DON'T PAY · NEW BANK A/C", "red"),
    ]
    checks = ["ADDS UP", "WORDS", "TWICE?", "VENDOR", "BANK", "NAME"]
    out = []
    # header
    out.append(f'<rect x="32" y="30" width="52" height="52" rx="16" fill="url(#sunk)"/>'
               f'<text x="58" y="66" text-anchor="middle" style="font-family:{LABEL};font-weight:700;font-size:26px;fill:{t["green_ink"]}">N</text>'
               f'<text x="100" y="62" style="font-size:34px;font-weight:800;letter-spacing:-1px">Nipta</text>'
               f'<text x="101" y="82" class="muted" style="font-size:14px;font-weight:600">Pay the right bill, to the right person.</text>')
    out.append(chip(560, 34, "CASHFREE SANDBOX", t["amber"], t["amber_ink"], t, w=150))
    out.append(chip(718, 34, "SARVAM AI", t["green"], t["green_ink"], t, w=130))
    out.append(f'<text x="848" y="80" text-anchor="end" class="muted" style="font-size:13px;font-weight:600">English · ಕನ್ನಡ · हिन्दी</text>')
    # board
    out.append(f'<rect x="32" y="104" width="816" height="214" rx="24" fill="url(#card)" filter="url(#raise)"/>')
    out.append(f'<text x="56" y="134" class="lbl">THIS FRIDAY · 3 BILLS</text>')
    cx0, step = 384, 40
    for i, c in enumerate(checks):
        out.append(f'<text x="{cx0 + i * step}" y="134" text-anchor="middle" class="lbl" style="font-size:8px;letter-spacing:.06em">{c}</text>')
    for r, (ini, vendor, ref, lang, amt, bad, verdict, ck) in enumerate(bills):
        y = 150 + r * 54
        css += appear(f"row{r}", 1 + r * 3, dx=-18)
        out.append(f'<g class="a row{r}"><rect x="48" y="{y}" width="784" height="44" rx="14" fill="url(#card)" filter="url(#key)"/>'
                   f'<circle cx="72" cy="{y + 22}" r="14" fill="url(#sunk)"/>'
                   f'<text x="72" y="{y + 26}" text-anchor="middle" style="font-family:{LABEL};font-weight:700;font-size:11px">{ini}</text>'
                   f'<text x="96" y="{y + 19}" style="font-weight:800;font-size:13.5px">{e(vendor)}</text>'
                   f'<text x="96" y="{y + 35}" class="muted mono" style="font-size:10.5px">{e(ref)} · {e(lang)}</text>'
                   f'<text x="356" y="{y + 27}" text-anchor="end" style="font-family:{LABEL};font-weight:600;font-size:14px">{amt}</text>')
        for i in range(6):
            x = cx0 + i * step
            col = t["green"] if i != bad else (t["amber"] if ck == "amber" else t["red"])
            name = f"d{r}{i}"
            css += appear(name, 14 + r * 12 + i * 1.6)
            out.append(f'<circle cx="{x}" cy="{y + 22}" r="7" fill="url(#sunk)"/>'
                       f'<g class="a {name}"><circle cx="{x}" cy="{y + 22}" r="6" fill="{col}" filter="url(#glow)" opacity="0.7"/>'
                       f'<circle cx="{x}" cy="{y + 22}" r="5" fill="{col}"/></g>')
        css += appear(f"v{r}", 52 + r * 2.5, dx=10)
        out.append(chip(610, y + 11, verdict, t[ck], t[f"{ck}_ink"], t, cls=f"a v{r}"))
        out.append("</g>")
    # payout strip
    css += appear("pay", 64, dy=8)
    css += (f"@keyframes press {{ 0%, 69% {{ transform: none; }} 70.5% {{ transform: translateY(4px); }} 72%, 100% {{ transform: none; }} }}\n"
            f".press {{ animation: press {T}s infinite; }}\n")
    css += appear("pend", 72, 83)
    css += appear("done", 84)
    out.append(f'<g class="a pay"><rect x="32" y="338" width="816" height="64" rx="22" fill="url(#sunk)"/>'
               f'<g class="press"><rect x="48" y="350" width="196" height="40" rx="13" fill="{t["cap_deep"]}" transform="translate(0,5)"/>'
               f'<rect x="48" y="350" width="196" height="40" rx="13" fill="url(#cap)"/>'
               f'<text x="146" y="375" text-anchor="middle" style="font-weight:800;font-size:14px;fill:#F4FFF8">Approve ₹3,200</text></g>'
               f'<text x="262" y="367" style="font-weight:700;font-size:13px">to Coimbatore Packing</text>'
               f'<text x="262" y="384" class="muted mono" style="font-size:11px">saved account ••6011 · one transfer ID</text>'
               f'<g class="a pend gone">{chip(620, 359, "CASHFREE · PENDING", t["amber"], t["amber_ink"], t, w=212)}</g>'
               f'<g class="a done">{chip(620, 359, "COMPLETED · POSTED ONCE", t["green"], t["green_ink"], t, w=212)}</g></g>')
    desc = ("Nipta reads three bills (Coimbatore Packing in Kannada, Kaveri Fasteners in Hindi, Eastern Hardware in English), "
            "runs six checks on each, marks the first safe to pay, the second a possible copy and the third blocked for a "
            "changed bank account, then pays ₹3,200 once through the Cashfree sandbox after the owner approves.")
    return head(w, h, t, "Nipta", desc, css) + "\n".join(out) + "\n</svg>\n"


# ---------------------------------------------------------------- flow

def flow(t: dict) -> str:
    w, h = 880, 330
    nodes = [  # (title, lines, bracket)
        ("Bills in", ["Photos, PDFs, scans", "ಕನ್ನಡ · हिन्दी", "English"], "MODEL READS"),
        ("Sarvam reads", ["Document AI job", "raw text + confidence", "never does the maths"], "MODEL READS"),
        ("Rules decide", ["integer paise", "unsure → review", "never a guess"], "CODE DECIDES"),
        ("You approve", ["one click on the", "exact snapshot", "voice can't approve"], "PERSON AUTHORIZES"),
        ("Cashfree pays", ["Payouts V2", "one transfer ID", "settled on COMPLETED"], "MONEY MOVES ONCE"),
    ]
    nw, gap, x0, y0 = 148, 19, 32, 112
    out, css = [], ""
    xs = [x0 + i * (nw + gap) for i in range(len(nodes))]
    # brackets
    groups = {}
    for i, n in enumerate(nodes):
        groups.setdefault(n[2], []).append(i)
    colors = {"MODEL READS": t["blue"], "CODE DECIDES": t["ink2"], "PERSON AUTHORIZES": t["green_ink"], "MONEY MOVES ONCE": t["amber_ink"]}
    for g, idx in groups.items():
        a, b = xs[idx[0]], xs[idx[-1]] + nw
        out.append(f'<path d="M{a + 6} 92 v-10 h{b - a - 12} v10" fill="none" stroke="{colors[g]}" stroke-width="1.6" opacity="0.7"/>'
                   f'<text x="{(a + b) / 2}" y="72" text-anchor="middle" class="lbl" style="fill:{colors[g]}">{g}</text>')
    # rail + travelling token
    rail = f"M{xs[0] + nw / 2} {y0 + 60} H{xs[-1] + nw / 2}"
    out.append(f'<path d="{rail}" stroke="{t["line"]}" stroke-width="3" stroke-dasharray="2 7" stroke-linecap="round" fill="none"/>')
    for i, (title, lines, _) in enumerate(nodes):
        x = xs[i]
        css += appear(f"n{i}", 2 + i * 6, dy=8)
        body = "".join(f'<text x="{x + 16}" y="{y0 + 68 + j * 17}" class="muted" style="font-size:11px;font-weight:600">{e(l)}</text>'
                       for j, l in enumerate(lines))
        out.append(f'<g class="a n{i}"><rect x="{x}" y="{y0}" width="{nw}" height="120" rx="20" fill="url(#card)" filter="url(#raise)"/>'
                   f'<circle cx="{x + 26}" cy="{y0 + 28}" r="13" fill="url(#sunk)"/>'
                   f'<text x="{x + 26}" y="{y0 + 32}" text-anchor="middle" style="font-family:{LABEL};font-weight:700;font-size:11px;fill:{t["green_ink"]}">{i + 1}</text>'
                   f'<text x="{x + 46}" y="{y0 + 33}" style="font-weight:800;font-size:13.5px">{e(title)}</text>{body}</g>')
    out.append(f'<g><circle r="7" fill="{t["green"]}" filter="url(#glow)" opacity="0.8"/><circle r="5" fill="{t["green"]}"/>'
               f'<animateMotion dur="{T / 2}s" repeatCount="indefinite" path="M{xs[0] + nw / 2} {y0 + 128} H{xs[-1] + nw / 2}"/></g>')
    # side notes
    css += appear("s1", 34) + appear("s2", 40)
    out.append(f'<g class="a s1"><rect x="32" y="262" width="400" height="44" rx="14" fill="url(#sunk)"/>'
               f'<text x="50" y="281" style="font-weight:800;font-size:12.5px">Ask by voice or text · asking never pays</text>'
               f'<text x="50" y="297" class="muted" style="font-size:11.5px;font-weight:600">Saaras listens, Bulbul answers, in ಕನ್ನಡ / हिन्दी / English.</text></g>')
    out.append(f'<g class="a s2"><rect x="448" y="262" width="400" height="44" rx="14" fill="url(#sunk)"/>'
               f'<text x="466" y="281" style="font-weight:800;font-size:12.5px">Bank account changed?</text>'
               f'<text x="466" y="297" class="muted" style="font-size:11.5px;font-weight:600">Blocked. Relay can call the vendor on the number on file.</text></g>')
    out.append(f'<text x="32" y="40" class="lbl">HOW A BILL BECOMES A PAYMENT</text>')
    desc = ("Pipeline: bills in Kannada, Hindi or English are read by Sarvam Document AI, deterministic rules decide in integer "
            "paise, the owner approves an exact snapshot with one click, and Cashfree Payouts V2 sends it once. Voice questions "
            "never pay; a changed bank account is blocked and Relay can call the vendor's registered number.")
    return head(w, h, t, "How Nipta works", desc, css) + "\n".join(out) + "\n</svg>\n"


# ---------------------------------------------------------------- six checks

def checks(t: dict) -> str:
    w, h = 880, 400
    items = [
        ("Adds up", "Lines add up to ₹6,000. Stated ₹6,000."),
        ("Words match", "“Six Thousand” = ₹6,000."),
        ("Not twice", "No earlier bill like this one."),
        ("Known vendor", "Eastern Hardware is approved."),
        ("Same bank account", "Bill: ••6655 · saved: ••1234."),
        ("Name confirmed", "Account holder matches the vendor."),
    ]
    out, css = [], ""
    out.append(f'<text x="32" y="44" class="lbl">EVERY BILL, SIX CHECKS</text>'
               f'<text x="32" y="74" style="font-weight:800;font-size:22px;letter-spacing:-.5px">Eastern Hardware · EH-040 · ₹6,000</text>')
    tw, th, gx, gy = 260, 92, 18, 16
    for i, (title, note) in enumerate(items):
        col, row = i % 3, i // 3
        x, y = 32 + col * (tw + gx), 96 + row * (th + gy)
        hold = title == "Same bank account"
        start = 6 + i * 7
        css += appear(f"c{i}", start)
        css += appear(f"w{i}", 1, start - 1)
        if hold:
            css += appear("holdbg", start)
            out.append(f'<rect x="{x}" y="{y}" width="{tw}" height="{th}" rx="18" fill="url(#card)" filter="url(#key)"/>'
                       f'<g class="a holdbg"><rect x="{x}" y="{y}" width="{tw}" height="{th}" rx="18" fill="url(#sunk)"/>'
                       f'<rect x="{x + 1}" y="{y + 1}" width="{tw - 2}" height="{th - 2}" rx="17" fill="none" stroke="{t["red"]}" stroke-width="2"/></g>')
        else:
            out.append(f'<rect x="{x}" y="{y}" width="{tw}" height="{th}" rx="18" fill="url(#card)" filter="url(#key)"/>')
        out.append(f'<text x="{x + 18}" y="{y + 30}" style="font-weight:800;font-size:14.5px">{e(title)}</text>')
        out.append(f'<g class="a w{i} gone">{chip(x + tw - 96, y + 14, "CHECKING", t["faint"], t["muted"], t, w=82)}</g>')
        status = ("HOLD", t["red"], t["red_ink"]) if hold else ("PASS", t["green"], t["green_ink"])
        out.append(f'<g class="a c{i}">{chip(x + tw - 72, y + 14, status[0], status[1], status[2], t, w=58)}'
                   f'<text x="{x + 18}" y="{y + 62}" class="muted" style="font-size:12px;font-weight:600">{e(note)}</text></g>')
    css += appear("verdict", 52, dy=8)
    out.append(f'<g class="a verdict"><rect x="32" y="310" width="816" height="64" rx="20" fill="url(#sunk)"/>'
               f'<text x="54" y="340" style="font-weight:800;font-size:20px;fill:{t["red_ink"]}">Don\'t pay this bill.</text>'
               f'<text x="54" y="360" class="muted" style="font-size:12.5px;font-weight:600">The account on the bill isn\'t the one you approved. No override: a new account is added only by you, in Vendors.</text></g>')
    desc = ("Six checks on invoice EH-040 from Eastern Hardware for ₹6,000: adds up, words match, not twice, known vendor and "
            "name confirmed pass; same bank account holds because the bill asks for an account ending 6655 while the saved one "
            "ends 1234. Verdict: don't pay this bill.")
    return head(w, h, t, "Six checks", desc, css) + "\n".join(out) + "\n</svg>\n"


# ---------------------------------------------------------------- never

def never(t: dict) -> str:
    w, h = 880, 268
    items = [
        (["No payment", "without your click"], ["Voice and chat only prepare.", "Approval is bound to the exact", "payee, amount and snapshot."]),
        (["No money to a", "changed account"], ["A new account on a bill blocks", "it, even if the bank says the", "account is valid."]),
        (["No second payout", "on a retry"], ["One attempt per approval, one", "transfer ID. A timeout becomes", "UNKNOWN, never a resend."]),
        (["Nothing “paid”", "without Cashfree"], ["Settled only on COMPLETED.", "A reversal adds a correcting", "entry; history stays."]),
    ]
    out, css = [f'<text x="32" y="44" class="lbl">WHAT NEVER HAPPENS</text>'], ""
    tw, gx = 195, 12
    for i, (title, lines) in enumerate(items):
        x, y = 32 + i * (tw + gx), 64
        css += (f"@keyframes tick{i} {{ 0%, {8 + i * 10}% {{ stroke-dashoffset: 30; }} {14 + i * 10}%, 95% {{ stroke-dashoffset: 0; }} 100% {{ stroke-dashoffset: 30; }} }}\n"
                f".tick{i} {{ stroke-dasharray: 30; animation: tick{i} {T}s ease-out infinite; }}\n")
        out.append(f'<rect x="{x}" y="{y}" width="{tw}" height="178" rx="20" fill="url(#card)" filter="url(#raise)"/>'
                   f'<circle cx="{x + 30}" cy="{y + 34}" r="16" fill="url(#sunk)"/>'
                   f'<path class="a tick{i}" d="M{x + 22} {y + 34} l6 6 l11 -12" fill="none" stroke="{t["green"]}" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"/>'
                   + "".join(f'<text x="{x + 18}" y="{y + 74 + k * 17}" style="font-weight:800;font-size:13.5px">{e(tl)}</text>' for k, tl in enumerate(title)))
        for j, line in enumerate(lines):
            out.append(f'<text x="{x + 18}" y="{y + 118 + j * 17}" class="muted" style="font-size:11.5px;font-weight:600">{e(line)}</text>')
    desc = ("Four guarantees: no payment without the owner's click; no money to a changed bank account; no second payout on "
            "retry; nothing marked paid without Cashfree's confirmation.")
    return head(w, h, t, "What never happens", desc, css) + "\n".join(out) + "\n</svg>\n"


if __name__ == "__main__":
    for name, fn in {"hero": hero, "flow": flow, "checks": checks, "never": never}.items():
        for theme, tokens in THEMES.items():
            (OUT / f"{name}-{theme}.svg").write_text(fn(tokens), encoding="utf-8")
            print(f"wrote docs/img/{name}-{theme}.svg")
