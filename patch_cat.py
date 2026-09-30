#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from pathlib import Path
import re, sys

def pick_html(arg=None):
    if arg:
        p=Path(arg)
        if p.exists(): return p
    candidates=[p for p in Path(".").glob("*.html") if "v14" in p.name.lower()]
    if not candidates:
        candidates=list(Path(".").glob("*.html"))
    if not candidates:
        raise SystemExit("HTML-файл не найден. Положите PATCH_CAT.bat рядом с прототипом v14.1 или перетащите HTML на BAT.")
    return max(candidates, key=lambda p:p.stat().st_mtime)

src=pick_html(sys.argv[1] if len(sys.argv)>1 else None)
text=src.read_text(encoding="utf-8",errors="ignore")

if "catTrackV10" not in text or "catRunnerV10" not in text:
    raise SystemExit("В этом HTML не найдена текущая сцена кота (catTrackV10/catRunnerV10).")

# 1) Disable the old scroll-scrub logic that freezes the cat mid-jump.
old_pattern = re.compile(
    r"\n\s*if\(catTrack && catRunner\)\{\s*"
    r"const r=catTrack\.getBoundingClientRect\(\);.*?"
    r"if\(p<\.18\) catLanded=false;\s*"
    r"\}\s*"
    r"raf=0;",
    re.S
)
replacement = "\n    raf=0;"
text2, n = old_pattern.subn(replacement, text, count=1)
if n != 1:
    raise SystemExit("Не удалось безопасно отключить старую scroll-анимацию кота. Файл не изменён.")
text = text2

css = r'''
/* v14.2 — кот: прыжок запускается скроллом, но НЕ управляется скроллом */
.cat-track-v10,
.v14-cat-track{
  overflow:visible!important;
}
.cat-runner-v10{
  transition:none!important;
  will-change:transform;
  transform-origin:50% 100%;
}
.cat-runner-v10 .cat-v10{
  object-fit:contain!important;
}
.cat-runner-v10.cat-jump-once{
  animation:catJumpV142 1.12s cubic-bezier(.22,.72,.28,1) forwards!important;
}
@keyframes catJumpV142{
  0%   {transform:translate3d(0,0,0) rotate(0deg) scale(1,1)}
  9%   {transform:translate3d(var(--jx0,10px),6px,0) rotate(-1deg) scale(1.07,.91)}
  26%  {transform:translate3d(var(--jx1,80px),var(--jy1,-58px),0) rotate(-4deg) scale(.98,1.03)}
  48%  {transform:translate3d(var(--jx2,170px),var(--jy2,-104px),0) rotate(1deg) scale(1,1)}
  68%  {transform:translate3d(var(--jx3,260px),var(--jy3,-76px),0) rotate(3deg) scale(1,1)}
  88%  {transform:translate3d(var(--jx4,335px),4px,0) rotate(1deg) scale(1.08,.91)}
  100% {transform:translate3d(var(--jx5,360px),0,0) rotate(0deg) scale(1,1)}
}
@media(max-width:720px){
  .cat-runner-v10.cat-jump-once{
    animation-duration:.92s!important;
  }
}
@media (prefers-reduced-motion: reduce){
  .cat-runner-v10.cat-jump-once{
    animation:none!important;
    transform:translate3d(var(--jx5,0px),0,0)!important;
  }
}
'''
text = text.replace("</style>", css + "\n</style>", 1)

js = r'''
  /* ---------------- v14.2 cat jump: trigger, not scrub ---------------- */
  (function(){
    const track=document.getElementById('catTrackV10');
    const runner=document.getElementById('catRunnerV10');
    if(!track || !runner) return;

    const frames=[...runner.querySelectorAll('.cat-v10')];
    // During the jump use one intact frame only: movement comes from the trajectory,
    // not from a scroll-bound frame sequence.
    if(frames.length){
      frames.forEach((f,i)=>f.classList.toggle('active',i===0));
    }

    function setJumpGeometry(){
      const maxX=Math.max(0,track.clientWidth-runner.offsetWidth-16);
      const mobile=window.matchMedia('(max-width:720px)').matches;
      const peak=mobile?72:108;
      runner.style.setProperty('--jx0',Math.round(maxX*.03)+'px');
      runner.style.setProperty('--jx1',Math.round(maxX*.22)+'px');
      runner.style.setProperty('--jx2',Math.round(maxX*.48)+'px');
      runner.style.setProperty('--jx3',Math.round(maxX*.72)+'px');
      runner.style.setProperty('--jx4',Math.round(maxX*.94)+'px');
      runner.style.setProperty('--jx5',Math.round(maxX)+'px');
      runner.style.setProperty('--jy1',Math.round(-peak*.58)+'px');
      runner.style.setProperty('--jy2',Math.round(-peak)+'px');
      runner.style.setProperty('--jy3',Math.round(-peak*.70)+'px');
    }

    setJumpGeometry();
    window.addEventListener('resize',setJumpGeometry,{passive:true});

    let started=false;
    const reduced=window.matchMedia('(prefers-reduced-motion: reduce)').matches;

    function launch(){
      if(started) return;
      started=true;
      setJumpGeometry();
      runner.classList.add('cat-jump-once');
      if(reduced){
        runner.style.transform='translate3d(var(--jx5),0,0)';
      }
    }

    const observer=new IntersectionObserver(entries=>{
      for(const entry of entries){
        if(entry.isIntersecting && entry.intersectionRatio>=0.55){
          launch();
          observer.disconnect();
          break;
        }
      }
    },{threshold:[0,.25,.55,.75]});

    observer.observe(track);

    runner.addEventListener('animationend',()=>{
      if(typeof landingSound==='function') landingSound();
    },{once:true});
  })();
'''
marker="  /* ---------------- data views ---------------- */"
if marker not in text:
    raise SystemExit("Не найдено место для вставки нового обработчика кота.")
text=text.replace(marker, js+"\n\n"+marker,1)

out=src.with_name(src.stem.replace("v14.1","v14.2")+" — кот trigger-jump.html")
out.write_text(text,encoding="utf-8")
print(f"Готово: {out.resolve()}")
