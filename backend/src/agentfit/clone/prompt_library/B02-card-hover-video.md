---
id: B02
slug: card-hover-video
group: gallery
title: Cards that play a muted video on hover or in view
title_it: Schede che riproducono un video muto al passaggio del mouse o quando sono in vista
summary: Gallery cards whose short muted loop plays while the card is pointed at, focused or, on a touch screen, in view, and rests on its poster otherwise.
summary_it: Schede di una galleria il cui breve video muto in loop parte quando la scheda viene indicata, riceve il focus o, su un touch screen, entra in vista, e altrimenti resta sulla sua immagine di copertina.
tags: [gallery, video, hover, performance]
size: M
skills: [motionsites-web-builder]
---

## Goal

A grid of cards, each with a short muted loop of the work it links to. At rest a card shows its poster; its clip plays while a pointer rests on it, while its link has keyboard focus or, on a touch screen, while most of it is in view, and stops when that ends. Motion measured on the reference: {{motion}}.

## Stack

- Each card: `<video data-card-video muted loop playsinline preload="none" poster="..." aria-hidden="true">` with no `autoplay` and no `controls`, next to the card's link, which carries the accessible name. Set `video.muted = true` in script too, as some browsers ignore the attribute.
- The poster is the clip's first frame at the clip's size.
- Clips: H.264 MP4 with `-movflags +faststart` and no audio track (`-an`), 6 to 12 s, at twice the card's largest rendered width. A card with no clip gets no `<video>`, no listener and no motion. To test without real clips, generate a placeholder locally, never fetched (canvas frames recorded with `MediaRecorder`, or `ffmpeg -f lavfi -i testsrc=duration=6:size=640x360:rate=24 -an`), with a canvas-drawn poster; list them as `[PLACEHOLDER: card clip]` in `docs/spec.md`.
- One controller for the grid. With `(hover: hover) and (pointer: fine)`: the card the pointer has rested on for 120 ms plays, and only one plays at a time. Keyboard focus on a card's link plays that card. On touch screens the code below plays the two cards most in view, each at least 60% visible.
- Every `play()` ends with `.catch(...)`: a refusal leaves the card on its poster without an error.
- When the tab is hidden (`visibilitychange`), every clip pauses.
- A title that sits on the clip gets a scrim: black at 60% at the bottom to transparent at half the card's height. A title below the media gets none.
- Cards with no clip go last in any default ordering used for testing; otherwise mark the first clip card in the build notes, so a clip-less card is not mistaken for broken hover.
- More technique notes: skill `motionsites-web-builder`, if installed; if not, use this text.

```ts
const ratios = new Map<HTMLVideoElement, number>();
const start = (v: HTMLVideoElement) => { if (v.paused) { v.muted = true; v.play().catch(() => {}); } };
const stop = (v: HTMLVideoElement) => { if (!v.paused) { v.pause(); v.currentTime = 0; } };
// Touch screens only, never under reduced motion: the two cards most in view play.
const observer = new IntersectionObserver((entries) => {
  for (const e of entries) ratios.set(e.target as HTMLVideoElement, e.intersectionRatio);
  const best = [...ratios].filter(([, r]) => r >= 0.6).sort((a, b) => b[1] - a[1]).slice(0, 2).map(([v]) => v);
  for (const v of ratios.keys()) (best.includes(v) ? start : stop)(v);
}, { threshold: [0, 0.3, 0.6, 0.9, 1] });
const touch = !matchMedia('(hover: hover) and (pointer: fine)').matches;
const still = matchMedia('(prefers-reduced-motion: reduce)').matches;
if (touch && !still) document.querySelectorAll<HTMLVideoElement>('[data-card-video]').forEach((v) => observer.observe(v));
```

## States

- **Poster**: on load and whenever playback stops. Left by a pointer resting 120 ms, focus on the link or, on touch, 60% in view. Why: nothing decodes.
- **Starting**: `play()` was called and no frame has come; the poster stays. Left on the `playing` event, or after 3 s without a frame, back to Poster. Why: no black frame.
- **Playing**: the muted loop runs. Left when the pointer leaves, the link loses focus, the card drops under 60% in view, another card starts on a pointer device, or the tab is hidden: the clip pauses and goes back to its first frame. Why: one moving card answers.
- **No clip**: a card with no clip stays as a plain link with its poster, or a flat placeholder. Why: nothing to play.
- **Refused**: `play()` was rejected or the file failed. The card keeps its poster and stays a working link; the next interaction may try again. Why: the browser decides.
- **Still** (reduced motion): nothing plays; the poster is the card. Why: no unasked motion.

## Motion

- The clip is the hover answer: no lift, zoom or tilt.
- The title underline appears in 180 ms `ease` on hover and focus.
- Reduced motion: posters only, nothing plays on hover, focus or scroll.

## Hard lines

- No `autoplay` attribute on a card's video: script starts it, so the same rules stop it.
- Never unmuted, never with controls.
- Never more than one clip playing on a pointer device, two on a touch screen.
- No clip loads before interest in its card.

## Verify

- Load the page with the network panel open: no clip is requested before you hover, focus or scroll a card into view on touch.
- Hover a card: it plays after the 120 ms rest; leave it: it stops on its first frame. Hover a second card: the first stops.
- Tab through the cards: each plays while focused.
- Hide the tab: every clip pauses.
- At 390 px with touch emulated, scroll: at most 2 clips play, and a card scrolled away is paused (`video.paused` is true in the console).
- With reduced motion emulated, nothing plays anywhere.
- A clip that fails to load leaves its card on the poster. The console shows no error except the failed-resource message of that media element.
- A card with no clip has no `<video>` and no listener.
- The checks above run on the generated placeholder clips, and the README says the clips are placeholders.
