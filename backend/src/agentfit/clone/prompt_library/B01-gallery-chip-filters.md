---
id: B01
slug: gallery-chip-filters
group: gallery
title: Grid or masonry gallery with chip filters and sort
title_it: Galleria a griglia o a mosaico con filtri a chip e ordinamento
summary: A gallery filtered by a row of chips and ordered by a sort menu without leaving the page, every state reachable by keyboard and announced.
summary_it: Una galleria filtrata da una fila di chip e ordinata da un menu senza lasciare la pagina, con ogni stato raggiungibile da tastiera e annunciato.
tags: [gallery, filters, chips, sort, accessibility]
size: M
---

## Goal

A row of filter chips above a grid of cards, with a sort menu beside the row. Pressing a chip shows only the cards of that category at once, in the page; the menu orders them. It works with a mouse, a finger, a keyboard and a screen reader, and its empty state is designed. Where the reference puts its gallery and how many cards it shows: {{sections}}.

## Stack

- Data: one typed list of cards (`id`, `title`, `category`, `date` in ISO 8601, `media` with `src`, `width`, `height`, `alt`).
- Chips: a `<button type="button" aria-pressed>` for each category, inside `<div role="group" aria-label="Filter by category">`: an "all" chip first, then 4 to 7 categories. One chip is pressed at a time (none only while the URL names an unknown category); pressing the pressed chip changes nothing.
- Sort: a native `<select>` labelled "Sort by": newest first (the default), oldest first, A to Z.
- The choice lives in the URL query (`?category=...&sort=...`): read on the first render, written with `history.replaceState` on every change (no navigation, no scroll), so a link restores the view. An unknown value gives the empty state, not an error.
- Grid: a `<ul>` of `<li>` cards in CSS grid, `repeat(auto-fill, minmax(<card min width>, 1fr))`. Compare with the reference by cards, columns, row height and gap, not total height. Columns, row height and gap: as the Site DNA block gives them, else decided and written in `docs/style_guide.md`. Show the card count the DNA gives, up to 24 (2 to 24 when it gives none), and state the expected height difference in the build notes.
- Masonry only when the reference's cards have mixed heights: keep one list in DOM order, `grid-auto-rows: 8px` with `row-gap: 0`, and give each card `align-self: start` and `grid-row: span N`, where `N = ceil((card height + gap) / 8)`, recomputed by a `ResizeObserver` on the grid. Never CSS `columns`: they break the sort order.
- Every picture and video has `width` and `height` (or `aspect-ratio`).
- A visually hidden `<p role="status">` states the result after each change, in a sentence such as "12 projects shown" or "No projects in this category".
- Under `(pointer: coarse)` every chip is at least 44 px tall. At 390 px the row wraps, gap 8 px, never scrolling sideways.

## States

- **All**: on load with no query, or by pressing the "all" chip or "Show all". Left by pressing a category chip. Why: shows everything.
- **Filtered**: by pressing a category chip, or loading `?category=` with a known value. Left by pressing another chip. Why: shareable by link.
- **Empty result**: the chosen category has no card, or the query names a category that does not exist. The grid keeps one row's height and shows one sentence saying what was not found and a "Show all" button. Left by "Show all" or another chip. Why: blank reads as broken.
- **Loading** (only when cards are fetched; for local data skip it and say in the build notes that nothing is fetched): skeleton cards with the cards' own ratio, two rows, the list marked `aria-busy="true"`. Left when the cards arrive. Why: no shift on landing.
- **Error** (only when cards are fetched; skipped for local data, for the same reason): the request failed, or 10 s passed without an answer. One sentence and a "Try again" button. Left by "Try again", back to Loading. Why: the person can act.

## Motion

- Chip: background and text colour change in 180 ms `ease`; on pointer devices, press `scale(0.97)` in 140 ms `cubic-bezier(0.22, 1, 0.36, 1)`.
- Result: the cards that enter are only those a filter or sort change newly shows (a card that stays shown does not move). The first load has no entrance. Entering cards fade from opacity 0 and rise 8 px in 240 ms `cubic-bezier(0.22, 1, 0.36, 1)`, the first four 50 ms apart, the rest together with the fourth. Leaving cards go at once.
- Reduced motion: opacity only, 120 ms; no rise, stagger or press scale.

## Hard lines

- A chip is a `<button>` with `aria-pressed`, never a link that reloads the page or a `<div>` with a click handler.
- Tab order: the chips, then the sort menu, then the cards. A card with a whole-card link overlay is one tab stop, the link; nothing inside it is focusable.
- Pressing a chip leaves the focus on the chip and never scrolls the page.
- The pressed chip differs by fill and contrast, never by font weight: a weight change reflows the row.
- Use the person's category names, never the reference's.

## Verify

- Keyboard only: Tab reaches every chip, then the sort menu, then one stop for each card; Enter and Space press a chip; after every press exactly one chip has `aria-pressed="true"`.
- The status text changes after each press and each sort.
- `?category=<a real one>` is filtered on the first paint; `?category=none-such` shows the empty state with "Show all". Screenshot both.
- If cards are fetched: with the network throttled, skeletons show and no layout shift is recorded when the cards replace them. For local data, write that Loading, Error and this check were skipped.
- At 390 px the chips wrap and the page does not scroll sideways.
- Going from a category back to All animates only the cards that were hidden. All to a category animates none: the rest vanish, survivors stay still.
- The column count at 1440 px is the measured one, or the decision is in the build notes.
- With reduced motion emulated, cards appear with no rise.
