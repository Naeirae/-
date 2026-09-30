#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from pathlib import Path
import sys

OUTPUT_NAME = "PROORGANIC — прототип сайта — v14.2 — кот без зависания.html"

OLD_START = "    if(catTrack && catRunner){\\n"
OLD_END = "    }\\n    raf=0;"

NEW_BLOCK = r"""    if(catTrack && catRunner && !catRunner.dataset.jumpReady){
      catRunner.dataset.jumpReady='1';
      const frames=[...catRunner.querySelectorAll('.cat-v10')];
      frames.forEach((f,i)=>f.classList.toggle('active',i===0));

      const prepareJump=()=>{
        const maxX=Math.max(0,catTrack.clientWidth-catRunner.offsetWidth-18);
        const mobile=window.matchMedia('(max-width:720px)').matches;
        const arc=mobile?54:76;
        catRunner.style.setProperty('--cat-x1', Math.round(maxX*.30)+'px');
        catRunner.style.setProperty('--cat-x2', Math.round(maxX*.58)+'px');
        catRunner.style.setProperty('--cat-x3', Math.round(maxX*.82)+'px');
        catRunner.style.setProperty('--cat-x-end', Math.round(maxX)+'px');
        catRunner.style.setProperty('--cat-y1', Math.round(-arc*.78)+'px');
        catRunner.style.setProperty('--cat-y2', Math.round(-arc)+'px');
        catRunner.style.setProperty('--cat-y3', Math.round(-arc*.42)+'px');
      };

      const runJump=()=>{
        if(catRunner.dataset.jumped==='1' || catRunner.dataset.jumping==='1') return;
        prepareJump();
        catRunner.dataset.jumping='1';
        catRunner.classList.remove('cat-jump-landed');
        void catRunner.offsetWidth;
        catRunner.classList.add('cat-jump-play');

        window.setTimeout(()=>{
          catRunner.classList.remove('cat-jump-play');
          catRunner.classList.add('cat-jump-landed');
          catRunner.dataset.jumping='0';
          catRunner.dataset.jumped='1';
          if(!reducedMotion) landingSound();
        },1120);
      };

      prepareJump();
      window.addEventListener('resize',prepareJump,{passive:true});

      if(reducedMotion){
        catRunner.classList.add('cat-jump-landed');
        catRunner.dataset.jumped='1';
      } else {
        const catObserver=new IntersectionObserver((entries)=>{
          entries.forEach(entry=>{
            if(entry.isIntersecting && entry.intersectionRatio>=.36){
              runJump();
              catObserver.disconnect();
            }
          });
        },{threshold:[.36,.5]});
        catObserver.observe(catTrack);
      }
    }
"""

CSS = r"""
<style id="cat-jump-event-fix">
.cat-track-v10,.v14-cat-track{overflow:visible!important}
.cat-runner-v10{overflow:visible!important;transform:translate3d(0,0,0);transform-origin:50% 88%;will-change:transform}
.cat-runner-v10 .cat-v10{opacity:0!important}
.cat-runner-v10 .cat-v10:first-child{opacity:1!important}
.cat-runner-v10.cat-jump-play{animation:catJumpEvent 1.08s cubic-bezier(.22,.72,.28,1) forwards}
.cat-runner-v10.cat-jump-landed{transform:translate3d(var(--cat-x-end,0px),0,0) rotate(0deg) scale(1)}
@keyframes catJumpEvent{
0%{transform:translate3d(0,0,0) rotate(0deg) scale(1)}
9%{transform:translate3d(10px,5px,0) rotate(-1deg) scale(1.055,.93)}
31%{transform:translate3d(var(--cat-x1,90px),var(--cat-y1,-58px),0) rotate(-5deg) scale(.985,1.02)}
55%{transform:translate3d(var(--cat-x2,180px),var(--cat-y2,-76px),0) rotate(2.5deg) scale(.99,1.015)}
80%{transform:translate3d(var(--cat-x3,250px),var(--cat-y3,-32px),0) rotate(3deg) scale(1)}
91%{transform:translate3d(var(--cat-x-end,300px),5px,0) rotate(0deg) scale(1.065,.925)}
96%{transform:translate3d(var(--cat-x-end,300px),-4px,0) rotate(0deg) scale(.985,1.02)}
100%{transform:translate3d(var(--cat-x-end,300px),0,0) rotate(0deg) scale(1)}
}
@media(max-width:720px){
  .cat-runner-v10{width:170px!important;height:120px!important}
  .cat-track-v10{height:190px!important}
}
@media(prefers-reduced-motion:reduce){
  .cat-runner-v10.cat-jump-play{animation:none!important;transform:translate3d(var(--cat-x-end,0px),0,0)!important}
}
</style>
"""

def find_source():
    if len(sys.argv) > 1:
        p = Path(sys.argv[1].strip('"'))
        if p.exists():
            return p

    roots = [Path.cwd(), Path.cwd().parent, Path.home() / "Downloads"]
    seen = set()

    for root in roots:
        if not root.exists():
            continue
        for pattern in ("PROORGANIC*v14*один файл*.html", "PROORGANIC*v14*.html", "*PROORGANIC*.html"):
            for p in root.glob(pattern):
                try:
                    key = p.resolve()
                except Exception:
                    key = p
                if key in seen or "v14.2" in p.name.lower():
                    continue
                seen.add(key)
                try:
                    text = p.read_text(encoding="utf-8", errors="ignore")
                    if 'id="catRunnerV10"' in text:
                        return p
                except Exception:
                    pass
    return None

def patch(path):
    text = path.read_text(encoding="utf-8")

    if "cat-jump-event-fix" in text:
        print("Файл уже содержит новый прыжок.")
        return path

    if 'id="catRunnerV10"' not in text:
        raise RuntimeError("Не найден catRunnerV10: это не нужный v14.")

    if OLD_START not in text or OLD_END not in text:
        raise RuntimeError("Не найден старый scroll-scrub блок кота.")

    s = text.index(OLD_START)
    e = text.index(OLD_END, s)
    text = text[:s] + NEW_BLOCK + text[e + len("    }\\n"):]

    if "</head>" not in text:
        raise RuntimeError("Не найден </head>.")

    text = text.replace("</head>", CSS + "\\n</head>", 1)

    out = path.with_name(OUTPUT_NAME)
    out.write_text(text, encoding="utf-8")

    print("Готово:")
    print(out)
    print()
    print("Исходник не перезаписан.")
    return out

def main():
    source = find_source()
    if source is None:
        print("Не нашла HTML PROORGANIC v14.")
        print("Положи APPLY_CAT_FIX.bat и patch_cat.py рядом с HTML и запусти снова.")
        return 2
    print("Найден исходник:")
    print(source)
    print()
    try:
        patch(source)
        return 0
    except Exception as e:
        print("ОШИБКА:", e)
        return 1

if __name__ == "__main__":
    raise SystemExit(main())
