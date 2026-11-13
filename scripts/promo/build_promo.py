# ruff: noqa  (a one-off generator, kept as it ran)
"""The marketing video (assets/gardener-promo.mp4) as a HyperFrames project: writes index.html and
compositions/*.html into $PROMO_DIR (default: promo/ next to this file). Screens, photos and the counts
(assets/ui/boxes.json) come from cap.py (a running `gardener serve` on a copy of a library);
lines.json holds each caption line and its seconds. `npx hyperframes render` in promo/ makes the file.
The committed file is that render without audio, small enough to upload on GitHub for the README player (10 MB limit):
`ffmpeg -i render.mp4 -an -c:v libx264 -crf 27 -preset slow -pix_fmt yuv420p -movflags +faststart gardener-promo.mp4`.
The film is silent: captions only."""
import json
import os
from pathlib import Path

P = Path(os.environ.get("PROMO_DIR") or Path(__file__).parent / "promo")
N = json.load(open(P / "assets/ui/boxes.json"))["n"]      # all, good, bin, species, added: what the screens show
lines = json.load(open(Path(__file__).parent / "lines.json"))
VO_IN = 0.4
starts, t = [], 0.0
for ln in lines:
    starts.append(round(t, 2))
    t += VO_IN + ln["dur"] + 0.35
TOTAL = round(starts[-1] + VO_IN + lines[-1]["dur"] + 2.4, 2)
ends = starts[1:] + [TOTAL]
NAMES = ["hook", "lib", "sort", "good", "bin", "proof", "local", "end"]

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

# 1 hook: the library floods in, the count, the question
tiles = "".join(f'<img id="$p{i}" src="assets/ph/p{i:02d}.jpg" style="position:absolute;left:{(i % 8) * 240}px;top:{(i // 8) * 180}px;width:240px;height:180px;object-fit:cover;opacity:0">' for i in range(48))
scene("hook", dur[0], f"""<div id="$grid" style="position:absolute;inset:0">{tiles}</div>
<div id="$shade" style="position:absolute;inset:0;background:rgba(14,15,16,.82);opacity:0"></div>
<div class="t" id="$n" style="top:300px;font-size:250px;font-weight:900;font-family:Inter,sans-serif;opacity:0"><span id="$num">0</span><span style="font-size:110px;font-family:'PingFang SC';font-weight:600"> 张</span></div>
<div class="t" id="$s" style="top:610px;font-size:60px;color:#c6c6c6;opacity:0">都在硬盘里</div>
<div class="t" id="$q" style="top:440px;font-size:130px;opacity:0"><span class="acc">好的</span>那几张，在哪？</div>""",
      """
for (let i = 0; i < 48; i++) { const k = (i * 29) % 48; if (k < 20) gsap.set('$p' + i, { opacity: 1 }); else tl.fromTo('$p' + i, { opacity: 0, scale: 1.15 }, { opacity: 1, scale: 1, duration: 0.22, ease: 'power3.out' }, 0.1 + (k - 20) * 0.05); }
tl.to('$shade', { opacity: 1, duration: 0.4, ease: 'power2.inOut' }, 1.7);
show('$n', 1.8, 0.25); show('$s', 2.1, 0.2);
const c = { v: 0 }; tl.to(c, { v: __ALL__, duration: 1.0, ease: 'power3.out', onUpdate: () => { document.querySelector('$num').textContent = Math.round(c.v).toLocaleString('en-US'); } }, 1.8);
hide('$n', 3.3); hide('$s', 3.3); show('$q', 3.5, 0.25);
""".replace("__ALL__", str(N["all"])), intro=False)

# 2 lib: the real folder page; one click on 加入图库; the same photos in the library
scene("lib", dur[1], """<div class="cam" id="$cam"><img src="assets/ui/fs.jpg"><img id="$i1" src="assets/ui/added.jpg" style="opacity:0"><div class="cur" id="$cur"></div></div>
<div class="chip" id="$c1" style="left:1090px;top:420px;opacity:0">选一个<span class="acc">文件夹</span></div>
<div class="chip" id="$c2" style="left:1130px;top:820px;opacity:0">不复制 · 不移动 · <span class="acc">马上可看</span></div>""",
      """
document.querySelector('$cur').innerHTML = CUR;
gsap.set('$cam', { transformOrigin: '0 0' }); gsap.set('$cur', { x: 1500, y: 1150 });
const go = (x, y, at, d) => tl.to('$cur', { x, y, duration: d || 0.5, ease: 'power3.inOut' }, at);
const click = at => { tl.to('$cur', { scale: 0.82, duration: 0.07, ease: 'power2.in' }, at - 0.07); tl.to('$cur', { scale: 1, duration: 0.16, ease: 'power3.out' }, at); };
show('$c1', 0.7); hide('$c1', 2.0);
cam('$cam', 2.0, 0.6, 620, 150, 2.4);
go(566, 20, 2.3, 0.7); click(3.3); flip('$i1', 3.3);
tl.to('$cur', { opacity: 0, duration: 0.2 }, 3.6);
cam('$cam', 3.7, 0.7, 960, 540, 1);
show('$c2', 4.6);
""")

# 3 sort: the species it named (the real page), then one burst as one group (the real review page)
scene("sort", dur[2], """<div class="cam" id="$cam"><img src="assets/ui/species.jpg"><img id="$i1" src="assets/ui/review.jpg" style="opacity:0"></div>
<div class="chip" id="$c1" style="left:1180px;top:300px;opacity:0">认出<span class="acc">物种</span></div>
<div class="chip" id="$c2" style="left:1180px;top:800px;opacity:0">一次连拍 = <span class="acc">一组</span></div>""",
      """
gsap.set('$cam', { transformOrigin: '0 0' });
cam('$cam', 0.3, 0.7, 700, 250, 1.8);
show('$c1', 1.1); hide('$c1', 2.9);
tl.set('$i1', { opacity: 1 }, 3.05); tl.set('$cam', { x: 0, y: 0, scale: 1 }, 3.05);
cam('$cam', 3.1, 0.6, 1066, 420, 1.55);
show('$c2', 3.8);
""")

# 4 good: the real 好片 page, then photos that are in it
pk = "".join(f'<img id="$g{i}" src="assets/ph/g{i:02d}.jpg" style="position:absolute;left:{120 + (i % 4) * 425}px;top:{320 + (i // 4) * 205}px;width:405px;height:190px;object-fit:cover;border-radius:10px;opacity:0">' for i in range(12))
scene("good", dur[3], f"""<div class="cam" id="$cam"><img src="assets/ui/good.jpg"></div>
<div id="$shade" style="position:absolute;inset:0;background:rgba(18,19,20,.94);opacity:0"></div>
<div class="t" id="$h" style="top:90px;font-size:92px;opacity:0"><span class="acc">好片</span></div>
<div class="t" id="$s" style="top:215px;font-size:46px;color:#c6c6c6;opacity:0">{N["all"]:,} 张里的 <span style="color:#fff;font-weight:600">{N["good"]}</span> 张</div>
{pk}""",
      """
gsap.set('$cam', { transformOrigin: '0 0' });
cam('$cam', 0.2, 0.7, 900, 200, 2.0);
tl.to('$shade', { opacity: 1, duration: 0.35, ease: 'power2.inOut' }, 1.9);
show('$h', 2.0, 0.2); show('$s', 2.4, 0.2);
for (let i = 0; i < 12; i++) tl.fromTo('$g' + i, { opacity: 0, scale: 0.9 }, { opacity: 1, scale: 1, duration: 0.3, ease: 'power3.out' }, 2.7 + i * 0.18);
""")

# 5 bin: a photo selected in the library, X, the real 垃圾箱 page and its 清空 button
scene("bin", dur[4], """<div class="cam" id="$cam"><img src="assets/ui/selected.jpg"><img id="$i1" src="assets/ui/bin.jpg" style="opacity:0">
<div id="$key" style="position:absolute;left:440px;top:420px;width:84px;height:84px;border-radius:14px;background:#ededed;color:#1a1a1a;font:800 50px Inter,sans-serif;display:grid;place-items:center;box-shadow:0 8px 0 #9a9a9a;opacity:0">X</div></div>
<div class="chip" id="$c1" style="left:1120px;top:330px;opacity:0">进<span class="acc">垃圾箱</span>，文件不动</div>
<div class="chip" id="$c2" style="left:980px;top:420px;opacity:0">清空时，才移到废纸篓</div>""",
      """
gsap.set('$cam', { transformOrigin: '0 0' });
cam('$cam', 0.2, 0.6, 520, 480, 2.0);
show('$key', 1.0, 0.15); tl.to('$key', { y: 6, boxShadow: '0 2px 0 #9a9a9a', duration: 0.08 }, 1.5); tl.to('$key', { y: 0, boxShadow: '0 8px 0 #9a9a9a', duration: 0.14 }, 1.62);
hide('$key', 1.95, 0.12);
tl.set('$i1', { opacity: 1 }, 2.1); tl.set('$cam', { x: 0, y: 0, scale: 1 }, 2.1);
show('$c1', 2.5); hide('$c1', 3.9);
cam('$cam', 3.9, 0.6, 1640, 120, 2.3);
show('$c2', 4.7);
""")

# 6 proof: the measured number, with what it was measured on
scene("proof", dur[5], """<div class="t" id="$k" style="top:150px;font-size:44px;color:#a8a8a8;opacity:0">iNaturalist 标准集 · 加州 64 个物种 · 1,571 张照片</div>
<div class="t" id="$n" style="top:280px;font-size:330px;font-weight:900;font-family:Inter,sans-serif;opacity:0"><span id="$num">0.0</span><span style="font-size:170px">%</span></div>
<div class="t" id="$l" style="top:690px;font-size:64px;opacity:0">鸟类 <span class="acc">首选正确率</span><span style="color:#a8a8a8;font-size:44px">（1,046 张）</span></div>
<div class="t" id="$m" style="top:810px;font-size:44px;color:#c6c6c6;opacity:0">定到种的照片里，97.1% 是对的</div>""",
      """
show('$k', 0.5, 0.25);
show('$n', 2.9, 0.2);
const c = { v: 0 }; tl.to(c, { v: 91.7, duration: 1.3, ease: 'power3.out', onUpdate: () => { document.querySelector('$num').textContent = c.v.toFixed(1); } }, 2.9);
show('$l', 3.2, 0.2); show('$m', 4.3, 0.25);
""")

# 7 local: nothing leaves, nothing touched, over the real compare page
scene("local", dur[6], """<div class="cam" id="$cam"><img src="assets/ui/compare.jpg"></div>
<div id="$shade" style="position:absolute;inset:0;background:rgba(16,17,18,.8);opacity:0"></div>
<div class="t" id="$a" style="top:330px;font-size:104px;opacity:0">照片<span class="acc">不离开</span>你的电脑</div>
<div class="t" id="$b" style="top:520px;font-size:104px;opacity:0">不经你确认，原片<span class="acc">一张不动</span></div>""",
      """
gsap.set('$cam', { transformOrigin: '0 0' });
cam('$cam', 0, D, 1000, 520, 1.12, 'none');
tl.to('$shade', { opacity: 1, duration: 0.35 }, 0.9);
show('$a', 1.1, 0.25); show('$b', 2.9, 0.25);
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

hosts = "".join(
    f'<div id="{n}" data-composition-id="{n}" data-composition-src="compositions/{n}.html" data-start="{s}" data-duration="{d}" data-track-index="1" data-track-kind="graphics" data-width="1920" data-height="1080" style="z-index:1"></div>\n'
    for n, s, d in zip(NAMES, starts, dur))
(P / "index.html").write_text(f"""<!doctype html>
<html lang="zh-CN">
<head><meta charset="UTF-8"><meta name="viewport" content="width=1920, height=1080">
<script src="https://cdn.jsdelivr.net/npm/gsap@3.15.0/dist/gsap.min.js"></script>
<style>*{{margin:0;padding:0;box-sizing:border-box}}html,body{{margin:0;width:1920px;height:1080px;overflow:hidden;background:#1c1c1c}}#root{{position:relative;width:1920px;height:1080px}}</style></head>
<body>
<div id="root" data-composition-id="main" data-start="0" data-duration="{TOTAL}" data-width="1920" data-height="1080">
{hosts}<div id="cap" data-composition-id="cap" data-composition-src="compositions/cap.html" data-start="0" data-duration="{TOTAL}" data-track-index="5" data-track-kind="captions" data-width="1920" data-height="1080" style="z-index:5"></div>
</div>
<script>window.__timelines["main"] = gsap.timeline({{ paused: true }});</script>
</body></html>
""")
print("total", TOTAL, "starts", starts, "groups", len(groups))
