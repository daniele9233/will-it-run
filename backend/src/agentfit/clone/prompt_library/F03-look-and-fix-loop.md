---
id: F03
slug: look-and-fix-loop
group: process
title: Look-and-fix loop on measured evidence
title_it: Ciclo di verifica e correzione su prove misurate
summary: "After the build: screenshots at 390, 768 and 1440 px, measured checks against the style guide, the three worst problems fixed in each round, three rounds at most."
summary_it: "Dopo la build: screenshot a 390, 768 e 1440 px, controlli misurati rispetto alla guida di stile, i tre problemi peggiori corretti a ogni giro, al massimo tre giri."
tags: [process, screenshots, review, verify]
size: S
skills: [playwright-cli]
---

## Goal

After the full build of gate 3, look at the page the way a visitor will, at three widths, and fix what the evidence shows: screenshots and measured values, never an impression. Each round fixes the three worst problems; the loop ends when a round finds nothing blocking and nothing outside the tolerances of step 4, or after three rounds. Do it after every other recipe of this prompt.

## Steps

1. Build for production and serve that build locally (the framework's build command, then its start command): check what will ship, not the development server.
2. In every round, shoot at 390 × 844, 768 × 1024 and 1440 × 900: the first screen, then each section scrolled into view (a page with few sections: one capture per section, plus the full page). Name the files `r<round>-<width>-<section>.png` and keep them in `docs/look-and-fix/`. With the installed skill `playwright-cli`: `playwright-cli open <address>`, `playwright-cli resize 390 844`, `playwright-cli screenshot --filename=r1-390-hero.png`, and `playwright-cli console` for the errors.
3. Look at every screenshot, then run each check of the shared Verify at each width. Overflow is measured, not guessed: `document.documentElement.scrollWidth - document.documentElement.clientWidth` is 0. Shoot the first screen once more with reduced motion (`playwright-cli set-reduced-motion reduce`, then reload).
4. Measure the page against `docs/style_guide.md` at 1440 × 900. A problem is: a text role whose computed `font-size` is more than 2 px from the guide; a background or text colour whose hex is not in the guide; a container more than 16 px wider or narrower than the guide; a section more than 15% taller or shorter than the guide says, with this exception: a gallery section is compared by cards, columns, row height and gap (the Site DNA block gives them as "N cards in C columns, rows ~H px tall, gap G px, section S px"), not by total height. Show the DNA's card count up to a cap of 24, or 2 to 24 cards when the DNA gives none (at least 2 full rows), never cards invented to reach the height. State the expected height difference (about (N minus shown) / C rows of H + G px) in `docs/look-and-fix.md` and the README. On any other miss, change the page and measure again.
5. If `docs/research/<site>/capture/` does not exist, compare against the numbers of the Site DNA block and say so in `docs/look-and-fix.md`. If it exists (an exact copy made by Will It Run), it has the original's full-page screenshots: `reference-desktop.png` at 1440 × 900 and `reference-mobile.png` at 390 × 844. Put your shots of the same width beside each (one full-page shot if your tool takes one, else the section shots in order) and compare the grammar only: the order of the sections, their relative heights, the edges text aligns to, the density. The text and the pictures are meant to differ.
6. Rank what you found. Blocking first: an error in the console, horizontal overflow, text under 4.5:1 contrast, a broken or missing state, motion that ignores reduced motion, content left hidden by a reveal that never fired. Then measured differences, then what only looks wrong. Write down the three worst, each with its evidence (a file or a value) and the exact change, in values, not adjectives.
7. Make those three changes, build again and shoot the same set with the next round number. Stop when a round finds nothing blocking and nothing outside the tolerances, or after round 3.
8. Keep `docs/look-and-fix.md`: for each round the shots, the problems with their evidence, the changes and what they did. Whatever is still open after the last round goes into the README.

## Hard lines

- A problem is fixed only when a screenshot or a measurement taken after the change shows it.
- Fix the page, never the guide: change `docs/style_guide.md` only for a mistake in it, and say so in the log.
- Never more than three changes in one round, so the next shots show what each one did.

## Verify

- `docs/look-and-fix.md` has one entry for each round, and each problem in it names its evidence and its change.
- The screenshots of the last round exist at all three widths.
- No problem is marked fixed without evidence taken after the change.
- A section outside 15% is either fixed or recorded as an expected difference caused by its card count.
- The README lists the problems left open and what was not checked.
