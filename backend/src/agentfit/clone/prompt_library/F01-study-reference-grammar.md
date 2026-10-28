---
id: F01
slug: study-reference-grammar
group: process
title: Study a reference into a style guide (grammar, never content)
title_it: Studiare un riferimento e ricavarne una guida di stile (la grammatica, mai il contenuto)
summary: Before any page code, the measured facts of the reference become docs/style_guide.md and docs/spec.md, every unknown is decided out loud, and the work stops for approval.
summary_it: Prima di scrivere codice, i dati misurati del riferimento diventano docs/style_guide.md e docs/spec.md, ogni valore sconosciuto viene deciso dichiarandolo e il lavoro si ferma per l'approvazione.
tags: [process, style-guide, spec, reference]
size: M
---

## Goal

Before any page code, turn what was measured on the reference into two documents: `docs/style_guide.md`, its grammar (colour roles, type, spacing, layout, sections, motion), and `docs/spec.md`, the page that will be built with that grammar. This is gate 1 of the shared rules, written out; do it before any other recipe of this prompt. The grammar transfers; the content (words, names, logos, pictures, footage, claims) never does.

## Steps

1. Read the measured facts. Host: {{host}}. Viewport: {{viewport}}. Palette: {{palette}}. Fonts: {{fonts}}. Type scale: {{type_scale}}. Spacing: {{spacing}}. Sections: {{sections}}. Motion: {{motion}}. Libraries: {{libs}}. A fact that reads Unknown was not measured: you decide it in step 3.
2. Split grammar from content. Keep sizes, ratios, colour roles and their shares of the surface, spacing steps, the order and height of the sections, durations and easings. Leave out every word, name, logo, picture and clip.
3. Write `docs/style_guide.md` with these headings, in this order:
   - Colour: each role (background, text, muted text, accent, line) with its hex, where it is used and its share of the surface; the contrast ratio of each text colour on its background, at least 4.5:1 for body text. If the Site DNA block says a measured text, muted or line colour is below 4.5:1, do not use it for body text: take the nearest darker or lighter value that reaches 4.5:1 and record it under Decisions as chosen, with the measured value and the ratio.
   - Type: each family with its fallback stack and its role; the steps of the scale: sort the sizes and merge any two less than 2 px apart into the larger one, recording each merge (12 and 13 become 13); a measured size with no neighbour within 2 px keeps its number. Each step in px at the measured width and as `clamp()`: the measured size is the maximum, the middle term is the measured size divided by the measured width, times 100, in vw, and the minimum is decided for 390 px.
   - Spacing: the base unit and its steps, in px.
   - Layout: the container width, columns, gutters and breakpoints.
   - Sections: one line for each, in order: its role, its height in px and in screens at the measured viewport, and what kind of thing it holds (a statement of 4 to 8 words, a grid of 12 cards), never what it says.
   - Motion: every moving thing with its trigger, duration in ms, easing, distance and stagger; what never moves; the reduced-motion version of each.
   - Components: buttons, chips, cards, inputs, menus: radius, padding, border, and the states each has.
   - Decisions: every fact that was Unknown, with the value chosen and the reason; every font substitution; every text, muted or line colour chosen to meet contrast, marked chosen, not measured.
4. Write `docs/spec.md`: the sections in order, holding the person's own content or `[PLACEHOLDER: what belongs here]`; for each interactive part its states (how each is entered and left, and why it exists); the asset list, each one real or a placeholder; the libraries with their versions: the version a recipe names wins; if none does, pin the latest stable release at build time (`npm view <package> version`) and write it here; and, as acceptance, the Verify lines of every recipe used.
5. Gate 1. If you can ask a person, stop and ask them to approve both files before any page code. If you cannot ask a person (an automated run), write every assumption in `docs/prompts/assumptions.md`, write "Not approved: continued without approval" at the top of `docs/spec.md`, and go on.

## Hard lines

- A measured value keeps its number: convert its unit, never round it into a nicer one.
- An Unknown is never filled with a value that looks measured: it goes under Decisions, marked as decided, with the reason.
- The style guide quotes no sentence of the reference and names no brand, product or person from it; the host appears once, as the site studied.
- A library the reference uses is a fact, not an order: install only what the recipes of this prompt name, at the versions of docs/spec.md.
- No page code, component or dependency before gate 1 is passed.

## Verify

- Both files exist, and the style guide has its eight headings in order.
- Every value in the style guide has a unit and a source: measured, or decided with its reason.
- Every fact that read Unknown in step 1 is under Decisions.
- A search of both files for the reference's brand name, its headlines and the names of its picture files finds nothing, the host line excepted.
- The contrast ratio is computed for every text colour against its background, and body text reaches 4.5:1 (a measured colour below it is replaced and recorded).
- Every library in `docs/spec.md` has a version, and `package.json` matches.
- The approval is recorded; or, if no person could be asked, the "Not approved" line opens `docs/spec.md` and `docs/prompts/assumptions.md` exists.
- Every font measured but not bundled or hotlinked is a recorded substitution (wanted, used, why); a font declared but not seen in visible text is optional; every colour not measured is marked chosen.
