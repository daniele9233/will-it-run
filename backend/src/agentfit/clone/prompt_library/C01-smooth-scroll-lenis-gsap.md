---
id: C01
slug: smooth-scroll-lenis-gsap
group: motion
title: Smooth scroll with Lenis, synced with GSAP ScrollTrigger
title_it: Scroll fluido con Lenis, sincronizzato con GSAP ScrollTrigger
summary: Wheel scrolling with a short glide on top of the native scroll, one frame loop for Lenis and GSAP, and every ScrollTrigger reading the position Lenis paints.
summary_it: Uno scroll con la rotella che scivola per un attimo sopra lo scroll nativo, un solo ciclo di frame per Lenis e GSAP, e ogni ScrollTrigger che legge la posizione disegnata da Lenis.
tags: [lenis, gsap, scrolltrigger, scroll]
size: M
libs: {lenis: "1.3.27", gsap: "3.13.0", "@gsap/react": "2.1.2"}
skills: [gsap-scrolltrigger, gsap-react]
---

## Goal

Give wheel and trackpad scrolling a short glide, while the page stays a native scroll: `position: sticky`, anchors, the keyboard, find in page and assistive technology keep working. Lenis and GSAP share one frame loop, so every ScrollTrigger animation reads the position Lenis has just painted. The libraries measured on the reference: {{libs}}.

## Stack

- Install exactly `lenis@1.3.27`, `gsap@3.13.0` and `@gsap/react@2.1.2`. Read the installed skills `gsap-scrolltrigger` and `gsap-react` before writing a ScrollTrigger.
- One client component, mounted once in the root layout around the whole app (below): one Lenis instance for the page.
- Options: `autoRaf: false` (GSAP's ticker drives Lenis); `lerp: 0.12` (Lenis's default is 0.1; between 0.1 and 0.14 the page has weight and still settles soon after the wheel stops); `anchors: true` (otherwise Lenis keeps in-page links from working while it scrolls); `stopInertiaOnNavigate: true` (a click on an internal link stops the glide). Leave `syncTouch` false, so touch scrolling stays native, and `respectReducedMotion` true.
- Every animation lives in `useGSAP(() => { ... }, { scope })` and is created inside `gsap.matchMedia()` under `(prefers-reduced-motion: no-preference)`; the callback returns `() => mm.revert()`. Nothing runs during server rendering.
- After the web fonts load, `ScrollTrigger.refresh()` (in the code below). Give every picture `width` and `height`, so a late image never moves a trigger.
- A dialog or menu that covers the page calls `lenis.stop()` when it opens and `lenis.start()` when it closes (the instance comes from `useLenis()`); its own scrolling panel gets `data-lenis-prevent`.

```tsx
'use client';
import { useEffect, useRef, type ReactNode } from 'react';
import gsap from 'gsap';
import { ScrollTrigger } from 'gsap/ScrollTrigger';
import { useGSAP } from '@gsap/react';
import { ReactLenis, useLenis, type LenisRef } from 'lenis/react';
import 'lenis/dist/lenis.css';

gsap.registerPlugin(ScrollTrigger, useGSAP);

export default function SmoothScroll({ children }: { children: ReactNode }) {
  const lenisRef = useRef<LenisRef>(null);
  useLenis(ScrollTrigger.update); // each Lenis scroll updates ScrollTrigger in the same frame
  useEffect(() => {
    const tick = (time: number) => lenisRef.current?.lenis?.raf(time * 1000); // GSAP counts seconds, Lenis milliseconds
    gsap.ticker.add(tick);
    gsap.ticker.lagSmoothing(0);
    document.fonts.ready.then(() => ScrollTrigger.refresh());
    return () => gsap.ticker.remove(tick);
  }, []);
  return (
    <>
      <ReactLenis root ref={lenisRef} options={{ autoRaf: false, lerp: 0.12, anchors: true, stopInertiaOnNavigate: true }} />
      {children}
    </>
  );
}
```

## States

- **Gliding**: wheel or trackpad input with motion allowed. Left when the scroll settles, a dialog opens or reduced motion is set. Why: the weight of the reference, without taking the scroll away from the person.
- **Native**: touch, keyboard and scrollbar input; reduced motion (Lenis forces `lerp` to 1 and makes programmatic scrolls instant); JavaScript off. Left by wheel input once motion is allowed. Why: never fight the device or the person's setting.
- **Stopped**: a dialog or menu is open (`lenis.stop()`). Left when it closes (`lenis.start()`). Why: the page under an overlay must not move while the overlay's own panel scrolls.
- **To an anchor**: an in-page link was followed. Left when the target is reached or the person scrolls. Why: anchors keep working.

## Motion

- Wheel: `lerp` 0.12, `wheelMultiplier` 1. Touch: native.
- Scroll-linked tweens: `scrub: true` and `ease: "none"`, locked to the scrollbar, with no second smoothing on top of Lenis.
- Reveals on scroll: `start: "top 85%"`, `once: true`; rise 24 px and fade in over 0.8 s with `expo.out`, 0.08 s apart, four elements at most in one sequence.
- Pins: about one screen of scroll per step, three screens at most.
- Reduced motion: Lenis stops smoothing by itself, and no tween is created, so every element sits in its final place.

## Hard lines

- Never `respectReducedMotion: false`, never `syncTouch: true`, never a second Lenis instance.
- No `ScrollTrigger.scrollerProxy`: Lenis keeps the native scroll, so the wiring above is all ScrollTrigger needs.
- No CSS scroll-snap on the page while Lenis runs: Lenis 1.3.27 does not support it (its `lenis/snap` does the snapping).
- Never animate a pinned element itself, only its children; never `scrub` and `toggleActions` on one trigger; no `markers` in the build.

## Verify

- `npm ls lenis gsap @gsap/react` lists 1.3.27, 3.13.0 and 2.1.2.
- In the console, `document.documentElement.className` contains `lenis`.
- Wheel: the page glides and stops soon after the wheel does. Keyboard (Space, Page Down, the arrows, Home, End) and touch at 390 px scroll natively.
- With `markers: true` on one trigger for the test, its markers and its element move together while you wheel, and a pinned section does not jitter. Remove the markers.
- An in-page link lands with its target in view, below any fixed header. With a dialog open, the wheel does not move the page and the dialog's own panel scrolls.
- With reduced motion emulated, the wheel moves the page with no glide and every revealed element is visible on load.
