# ruff: noqa  (a one-off generator, kept as it ran)
"""The marketing video (assets/gardener-promo.mp4) as a HyperFrames project: writes promo/index.html and
promo/compositions/*.html next to this file. Screens and photos come from cap.py (a running `gardener serve`);
lines.json holds each caption line and its seconds. `npx hyperframes render` in promo/ makes the file.
PROMO_VOICE=1 adds assets/vo/l<N>.wav and assets/bgm/track.wav: only audio you may publish (not macOS system voices)."""
import json
import os
from pathlib import Path

P = Path(__file__).parent / "promo"
lines = json.load(open(Path(__file__).parent / "lines.json"))
VO_IN = 0.4
starts, t = [], 0.0
for ln in lines:
    starts.append(round(t, 2))
    t += VO_IN + ln["dur"] + 0.35
TOTAL = round(starts[-1] + VO_IN + lines[-1]["dur"] + 2.4, 2)
ends = starts[1:] + [TOTAL]
NAMES = ["hook", "scan", "review", "batch", "picks", "proof", "local", "end"]

CSS = """
#root{position:absolute;inset:0;overflow:hidden;font-family:'PingFang SC','Inter',sans-serif;color:#ededed}
.bg{position:absolute;inset:0;background:#1c1c1c}
.stage{position:absolute;inset:0}
.cam{position:absolute;left:0;top:0;width:1920px;height:1080px}
.cam img{position:absolute;left:0;top:0;width:1920px;height:1080px}
.t{position:absolute;left:0;width:1920px;text-align:center;font-weight:600;letter-spacing:.01em}
.chip{position:absolute;padding:14px 30px;border-radius:14px;background:rgba(18,20,21,.92);border:2px solid rgba(255,255,255,.22);font-size:40px;font-weight:600;white-space:nowrap}
.acc{color:#93d29a}
.cur{position:absolute;left:0;top:0;width:44px;height:44px;z-index:9}
"""
FONT = "@font-face{font-family:'PingFang SC';src:local('PingFang SC'),local('PingFangSC-Regular');font-weight:400}@font-face{font-family:'PingFang SC';src:local('PingFang SC Semibold'),local('PingFangSC-Semibold');font-weight:600}"
JS = """
const tl = gsap.timeline({ paused: true });
const D = __DUR__;
const cam = (sel, at, dur, cx, cy, s, ease) => { const x = Math.min(0, Math.max(1920 - 1920 * s, 960 - s * cx)), y = Math.min(0, Math.max(1080 - 1080 * s, 540 - s * cy)); tl.to(sel, { x, y, scale: s, duration: dur, ease: ease || 'power2.inOut' }, at); };
const show = (sel, at, d) => tl.fromTo(sel, { opacity: 0, filter: 'blur(8px)' }, { opacity: 1, filter: 'blur(0px)', duration: d || 0.2, ease: 'power3.out' }, at);
const hide = (sel, at, d) => tl.to(sel, { opacity: 0, filter: 'blur(8px)', duration: d || 0.15, ease: 'power2.in' }, at);
const flip = (sel, at) => tl.set(sel, { opacity: 1 }, at);
const CUR = '<svg viewBox="0 0 24 24" width="44" height="44"><path d="M4 2l15 9-6.5 1.6L9 19z" fill="#fff" stroke="#111" stroke-width="1.4" stroke-linejoin="round"/></svg>';
"""


def scene(name, dur, body, script, intro=True, outro=True):
    sid = name
    js = JS.replace("__DUR__", str(dur)) + f"gsap.set('#{sid}-st', {{ transformOrigin: '0 0' }});\n"
    if intro:
        js += f"show('#{sid}-st', 0, 0.25);\n"
    else:
        js += f"tl.set('#{sid}-st', {{ opacity: 1 }}, 0);\n"
    js += script.replace("$", f"#{sid}-")
    if outro:
        js += f"\nhide('#{sid}-st', D - 0.16, 0.15);"
    js += f"\ntl.set({{}}, {{}}, D);\nwindow.__timelines['{sid}'] = tl;"
    html = f"""<template>
<style>{FONT}{CSS.replace('#root', '#root')}</style>
<div id="root" data-composition-id="{sid}" data-width="1920" data-height="1080">
<div class="bg clip" id="{sid}-bg" data-start="0" data-duration="{dur}" data-track-index="0"></div>
<div class="clip" id="{sid}-clip" data-start="0" data-duration="{dur}" data-track-index="1" style="position:absolute;inset:0" data-layout-allow-overflow>
<div class="stage" id="{sid}-st" style="opacity:{0 if intro else 1}">{body.replace('$', sid + '-')}</div></div>
</div>
<script>{js}</script>
</template>
"""
    (P / "compositions").mkdir(exist_ok=True)
    (P / f"compositions/{sid}.html").write_text(html)


dur = [round(e - s, 2) for s, e in zip(starts, ends)]

# 1 hook: the shoot floods in, the count, the question
tiles = "".join(f'<img id="$p{i}" src="assets/ph/p{i:02d}.jpg" style="position:absolute;left:{(i % 8) * 240}px;top:{(i // 8) * 180}px;width:240px;height:180px;object-fit:cover;opacity:0">' for i in range(48))
scene("hook", dur[0], f"""<div id="$grid" style="position:absolute;inset:0">{tiles}</div>
<div id="$shade" style="position:absolute;inset:0;background:rgba(14,15,16,.82);opacity:0"></div>
<div class="t" id="$n" style="top:300px;font-size:250px;font-weight:900;font-family:Inter,sans-serif;opacity:0"><span id="$num">0</span><span style="font-size:110px;font-family:'PingFang SC';font-weight:600"> 张</span></div>
<div class="t" id="$s" style="top:610px;font-size:60px;color:#c6c6c6;opacity:0">一天，一张卡</div>
<div class="t" id="$q" style="top:440px;font-size:130px;opacity:0">哪些值得<span class="acc">留下</span>？</div>""",
      """
for (let i = 0; i < 48; i++) { const k = (i * 29) % 48; if (k < 20) gsap.set('$p' + i, { opacity: 1 }); else tl.fromTo('$p' + i, { opacity: 0, scale: 1.15 }, { opacity: 1, scale: 1, duration: 0.22, ease: 'power3.out' }, 0.1 + (k - 20) * 0.05); }
tl.to('$shade', { opacity: 1, duration: 0.4, ease: 'power2.inOut' }, 1.9);
show('$n', 2.0, 0.25); show('$s', 2.3, 0.2);
const c = { v: 0 }; tl.to(c, { v: 1361, duration: 1.1, ease: 'power3.out', onUpdate: () => { document.querySelector('$num').textContent = Math.round(c.v).toLocaleString('en-US'); } }, 2.0);
hide('$n', 3.75); hide('$s', 3.75); show('$q', 3.95, 0.25);
""", intro=False)

# 2 scan: the real scan page; the three lanes; the species it found
scene("scan", dur[1], """<div class="cam" id="$cam"><img src="assets/ui/scan.jpg"></div>
<div class="chip" id="$c1" style="left:150px;top:660px;opacity:0">生成预览</div>
<div class="chip" id="$c2" style="left:790px;top:660px;opacity:0">找出动物</div>
<div class="chip" id="$c3" style="left:1420px;top:660px;opacity:0"><span class="acc">认出物种</span></div>""",
      """
gsap.set('$cam', { transformOrigin: '0 0', scale: 1.06, x: -58, y: -32 });
cam('$cam', 0, 1.2, 960, 540, 1, 'power3.out');
cam('$cam', 2.6, 0.6, 900, 245, 2.05);
show('$c1', 3.5); show('$c2', 4.5); show('$c3', 5.4);
hide('$c1', D - 1.05); hide('$c2', D - 1.05); hide('$c3', D - 1.05);
cam('$cam', D - 1.0, 0.55, 1730, 420, 1.9);
""")

# 3 review: by species (the rail), one burst is one group (the tiles)
scene("review", dur[2], """<div class="cam" id="$cam"><img src="assets/ui/review.jpg"></div>
<div class="chip" id="$c1" style="left:1010px;top:250px;opacity:0">按<span class="acc">物种</span>排好</div>
<div class="chip" id="$c2" style="left:1180px;top:800px;opacity:0">一次连拍 = <span class="acc">一组</span></div>""",
      """
gsap.set('$cam', { transformOrigin: '0 0' });
cam('$cam', 0.25, 0.6, 520, 500, 1.75);
show('$c1', 0.9); hide('$c1', 2.25);
cam('$cam', 2.3, 0.6, 1066, 420, 1.55);
show('$c2', 3.0); hide('$c2', D - 0.3);
""", outro=False)

# 4 batch: tick, shift-tick, 完成; tick two, 全部不要. Screens are the real states; only the first tick of each pair is drawn.
tick = lambda i, x, y: f'<div id="$k{i}" style="position:absolute;left:{x}px;top:{y}px;width:24px;height:24px;border-radius:5px;background:#e6e6e6;opacity:0;display:grid;place-items:center"><svg viewBox="0 0 24 24" width="15" height="15"><path d="M5 12.5l4.5 4.5L19 7.5" fill="none" stroke="#1a1a1a" stroke-width="3.2" stroke-linecap="round" stroke-linejoin="round"/></svg></div>'
scene("batch", dur[3], f"""<div class="cam" id="$cam"><img src="assets/ui/review.jpg"><img id="$i1" src="assets/ui/selected.jpg" style="opacity:0"><img id="$i2" src="assets/ui/done.jpg" style="opacity:0"><img id="$i3" src="assets/ui/selected2.jpg" style="opacity:0"><img id="$i4" src="assets/ui/rejected.jpg" style="opacity:0">
{tick(0, 562, 468)}{tick(1, 1386, 468)}
<div id="$sh" style="position:absolute;left:1120px;top:520px;padding:4px 12px;border-radius:7px;background:#ededed;color:#1a1a1a;font:700 20px Inter,sans-serif;opacity:0">Shift</div>
<div class="cur" id="$cur"></div></div>
<div class="chip" id="$u" style="left:1250px;top:880px;opacity:0">↩ 每一步都可以<span class="acc">撤销</span></div>""",
      """
document.querySelector('$cur').innerHTML = CUR;
gsap.set('$cam', { transformOrigin: '0 0', x: Math.min(0, 960 - 1.55 * 1066), y: Math.min(0, Math.max(1080 - 1080 * 1.55, 540 - 1.55 * 420)), scale: 1.55 });
gsap.set('$cur', { x: 1500, y: 760 });
const go = (x, y, at, d) => tl.to('$cur', { x, y, duration: d || 0.42, ease: 'power3.inOut' }, at);
const click = at => { tl.to('$cur', { scale: 0.82, duration: 0.07, ease: 'power2.in' }, at - 0.07); tl.to('$cur', { scale: 1, duration: 0.16, ease: 'power3.out' }, at); };
go(570, 476, 0.3, 0.5); click(0.85); flip('$k0', 0.85);
go(1188, 476, 0.95); show('$sh', 1.05, 0.12); click(1.5); flip('$i1', 1.5); tl.set('$k0', { opacity: 0 }, 1.5); hide('$sh', 1.75, 0.12);
go(745, 246, 1.75); click(2.45); flip('$i2', 2.45);
go(1394, 476, 2.9, 0.5); click(3.5); flip('$k1', 3.5);
go(570, 633, 3.6, 0.45); click(4.15); flip('$i3', 4.15); tl.set('$k1', { opacity: 0 }, 4.15);
go(918, 246, 4.3); click(4.9); flip('$i4', 4.9);
go(1300, 700, 5.1, 0.6);
show('$u', 5.3);
""", intro=False)

# 5 picks: the real collection page, then the picked photos
pk = "".join(f'<img id="$g{i}" src="assets/ph/p{j:02d}.jpg" style="position:absolute;left:{120 + (i % 4) * 425}px;top:{300 + (i // 4) * 205}px;width:405px;height:190px;object-fit:cover;border-radius:10px;opacity:0">' for i, j in enumerate([3, 19, 7, 27, 16, 43, 30, 23, 37, 10, 22, 41]))
scene("picks", dur[4], f"""<div class="cam" id="$cam"><img src="assets/ui/picks.jpg"></div>
<div id="$shade" style="position:absolute;inset:0;background:rgba(18,19,20,.94);opacity:0"></div>
<div class="t" id="$h" style="top:110px;font-size:92px;opacity:0"><span class="acc">AI 选鸟</span></div>
<div class="t" id="$s" style="top:228px;font-size:42px;color:#c6c6c6;opacity:0">每个物种 · 不同瞬间 · 最好的几张</div>
{pk}""",
      """
gsap.set('$cam', { transformOrigin: '0 0' });
cam('$cam', 0.2, 0.7, 560, 150, 2.1);
tl.to('$shade', { opacity: 1, duration: 0.35, ease: 'power2.inOut' }, 1.75);
show('$h', 1.85, 0.2); show('$s', 2.3, 0.2);
for (let i = 0; i < 12; i++) tl.fromTo('$g' + i, { opacity: 0, scale: 0.9 }, { opacity: 1, scale: 1, duration: 0.3, ease: 'power3.out' }, 2.5 + i * 0.2);
""")

# 6 proof: the measured number, with what it was measured on
scene("proof", dur[5], """<div class="t" id="$k" style="top:150px;font-size:44px;color:#a8a8a8;opacity:0">iNaturalist 标准集 · 加州 64 个物种 · 1,571 张照片</div>
<div class="t" id="$n" style="top:280px;font-size:330px;font-weight:900;font-family:Inter,sans-serif;opacity:0"><span id="$num">0.0</span><span style="font-size:170px">%</span></div>
<div class="t" id="$l" style="top:690px;font-size:64px;opacity:0">鸟类 <span class="acc">首选正确率</span><span style="color:#a8a8a8;font-size:44px">（1,046 张）</span></div>
<div class="t" id="$m" style="top:810px;font-size:44px;color:#c6c6c6;opacity:0">定到种的照片里，97.1% 是对的</div>""",
      """
show('$k', 0.5, 0.25);
show('$n', 3.3, 0.2);
const c = { v: 0 }; tl.to(c, { v: 91.7, duration: 1.3, ease: 'power3.out', onUpdate: () => { document.querySelector('$num').textContent = c.v.toFixed(1); } }, 3.3);
show('$l', 3.6, 0.2); show('$m', 4.7, 0.25);
""")

# 7 local: nothing leaves, nothing deleted, then the real 送到 Lightroom button
scene("local", dur[6], """<div class="cam" id="$cam"><img src="assets/ui/compare.jpg"></div>
<div id="$shade" style="position:absolute;inset:0;background:rgba(16,17,18,.8);opacity:0"></div>
<div class="t" id="$a" style="top:330px;font-size:104px;opacity:0">照片<span class="acc">不离开</span>你的电脑</div>
<div class="t" id="$b" style="top:520px;font-size:104px;opacity:0">不经你确认，原片<span class="acc">一张不动</span></div>
<div class="cam" id="$cam2" style="opacity:0"><img src="assets/ui/picks.jpg"></div>
<div class="chip" id="$c" style="left:240px;top:330px;opacity:0">选好了，再交给 <span class="acc">Lightroom</span></div>""",
      """
gsap.set('$cam', { transformOrigin: '0 0' }); gsap.set('$cam2', { transformOrigin: '0 0', x: 1920 - 1920 * 2.4, y: 0, scale: 2.4 });
cam('$cam', 0, D, 1000, 520, 1.12, 'none');
tl.to('$shade', { opacity: 1, duration: 0.35 }, 0.25);
show('$a', 0.45, 0.25); show('$b', 2.2, 0.25);
hide('$a', 3.75); hide('$b', 3.75);
tl.fromTo('$cam2', { opacity: 0, filter: 'blur(10px)' }, { opacity: 1, filter: 'blur(0px)', duration: 0.25, ease: 'power3.out' }, 3.95);
show('$c', 4.5);
""")

# 8 end card: the product's own mark, the name, where to get it
scene("end", dur[7], """<img id="$logo" src="assets/icon.svg" style="position:absolute;left:560px;top:370px;width:170px;height:170px;opacity:0">
<div id="$w" style="position:absolute;left:770px;top:362px;font:900 170px Inter,sans-serif;letter-spacing:-.03em;opacity:0">Gardener</div>
<div class="t" id="$s" style="top:600px;font-size:54px;color:#c6c6c6;opacity:0">开源 · 在你的 Mac 上本地运行</div>
<div class="t" id="$u" style="top:720px;font:600 46px 'JetBrains Mono',monospace;opacity:0"><span class="acc">github.com/lincleejun/gardener</span></div>""",
      """
tl.fromTo('$logo', { opacity: 0, scale: 0.6, rotation: -40 }, { opacity: 1, scale: 1, rotation: 0, duration: 0.5, ease: 'back.out(1.4)' }, 0.25);
tl.fromTo('$w', { opacity: 0, clipPath: 'inset(0 100% 0 0)' }, { opacity: 1, clipPath: 'inset(0 0% 0 0)', duration: 0.45, ease: 'power3.out' }, 0.55);
show('$s', 1.5, 0.25); show('$u', 2.3, 0.25);
""", outro=False)

# captions: each line split at its punctuation, time shared by character count
groups = []
for s, ln in zip(starts, lines):
    parts, cur = [], ""
    for ch in ln["text"]:
        cur += ch
        if ch in "，。？：":
            parts.append(cur)
            cur = ""
    if cur:
        parts.append(cur)
    n = sum(len(p) for p in parts)
    at = s + VO_IN
    for p in parts:
        d = ln["dur"] * len(p) / n
        groups.append((round(at, 2), round(at + d, 2), p.strip("，。：")))
        at += d
cap = "".join(f'<div class="cg" id="cap-g{i}"><span>{txt}</span></div>' for i, (_, _, txt) in enumerate(groups))
capjs = "const tl = gsap.timeline({ paused: true });\n" + "".join(
    f"tl.fromTo('#cap-g{i}', {{ opacity: 0 }}, {{ opacity: 1, duration: 0.1 }}, {a}); tl.to('#cap-g{i}', {{ opacity: 0, duration: 0.1 }}, {round(b - 0.1, 2)}); tl.set('#cap-g{i}', {{ opacity: 0, visibility: 'hidden' }}, {b});\n"
    for i, (a, b, _) in enumerate(groups)) + f"tl.set({{}}, {{}}, {TOTAL});\nwindow.__timelines['cap'] = tl;"
(P / "compositions/cap.html").write_text(f"""<template>
<style>{FONT}#root{{position:absolute;inset:0;font-family:'PingFang SC',sans-serif}}
.cg{{position:absolute;left:0;bottom:64px;width:1920px;text-align:center;opacity:0}}
.cg span{{display:inline-block;padding:10px 30px;border-radius:12px;background:rgba(10,11,12,.78);color:#fff;font-size:46px;font-weight:600}}</style>
<div id="root" data-composition-id="cap" data-width="1920" data-height="1080">
<div class="clip" id="cap-all" data-start="0" data-duration="{TOTAL}" data-track-index="0" style="position:absolute;inset:0" data-layout-allow-occlusion data-layout-allow-caption-zone>{cap}</div>
</div>
<script>{capjs}</script>
</template>
""")

clicks = [starts[3] + x for x in (0.85, 1.5, 2.45, 3.5, 4.15, 4.9)]
hosts = "".join(
    f'<div id="{n}" data-composition-id="{n}" data-composition-src="compositions/{n}.html" data-start="{s}" data-duration="{d}" data-track-index="1" data-track-kind="graphics" data-width="1920" data-height="1080" style="z-index:1"></div>\n'
    for n, s, d in zip(NAMES, starts, dur))
VOICE = os.environ.get("PROMO_VOICE") == "1"      # the wavs under assets/vo must be ones you may publish
vo = "" if not VOICE else "".join(f'<audio id="vo{i + 1}" class="clip" src="assets/vo/l{i + 1}.wav" data-start="{round(s + VO_IN, 2)}" data-duration="{lines[i]['dur']}" data-track-index="{10}" data-volume="1"></audio>\n' for i, s in enumerate(starts))
sfx = "".join(f'<audio id="ck{i}" class="clip" src="assets/sfx/click-soft.mp3" data-start="{round(c + 0.02, 2)}" data-duration="0.3" data-track-index="11" data-volume="0.4"></audio>\n' for i, c in enumerate(clicks))
bgm = f'<audio id="bgm" class="clip" src="assets/bgm/track.wav" data-start="0" data-duration="{TOTAL}" data-track-index="12" data-volume="0.14" data-fade-in="0.6" data-fade-out="2"></audio>\n' if VOICE and (P / "assets/bgm/track.wav").exists() else ""
(P / "index.html").write_text(f"""<!doctype html>
<html lang="zh-CN">
<head><meta charset="UTF-8"><meta name="viewport" content="width=1920, height=1080">
<script src="https://cdn.jsdelivr.net/npm/gsap@3.15.0/dist/gsap.min.js"></script>
<style>*{{margin:0;padding:0;box-sizing:border-box}}html,body{{margin:0;width:1920px;height:1080px;overflow:hidden;background:#1c1c1c}}#root{{position:relative;width:1920px;height:1080px}}</style></head>
<body>
<div id="root" data-composition-id="main" data-start="0" data-duration="{TOTAL}" data-width="1920" data-height="1080">
{hosts}<div id="cap" data-composition-id="cap" data-composition-src="compositions/cap.html" data-start="0" data-duration="{TOTAL}" data-track-index="5" data-track-kind="captions" data-width="1920" data-height="1080" style="z-index:5"></div>
{vo}{sfx}{bgm}</div>
<script>window.__timelines["main"] = gsap.timeline({{ paused: true }});</script>
</body></html>
""")
print("total", TOTAL, "starts", starts, "groups", len(groups), "bgm", bool(bgm))
