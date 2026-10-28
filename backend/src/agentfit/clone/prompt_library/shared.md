## Gates

Work in three gates, in this order. Do not start a gate before the one before it is done.

1. Write `docs/style_guide.md` and `docs/spec.md` from the DNA of the reference (the measured facts in this prompt). Then, if you can ask a person, stop and ask them to approve both before you write any page code. If you cannot ask a person (an automated run), write every assumption you made in `docs/prompts/assumptions.md` and go on.
2. Before the full build, make a still of every section: a screenshot of it. Look at each still against `docs/style_guide.md` and fix what is wrong.
3. Build the whole page, with its motion and states, then run every check under Verify.

## Assets

- Use real assets only: files the person gave you, or media you are free to use, saved into the project.
- Where there is no real asset, leave a visible placeholder marked `[PLACEHOLDER: what belongs here]` instead of inventing one.
- Never invent product screens, metrics, logos, names or testimonials.
- Do not hotlink third-party media: no image, video or audio may load from another site's address.
- Test placeholders may be generated locally (a canvas-drawn poster, a tiny generated clip), never fetched, and are marked as placeholders.
- Fonts: a measured font whose file you may not bundle or hotlink: use the nearest installed or system stack, and record the substitution (wanted, used, why) in the style guide and the README. A font declared but not seen in visible text is optional.
- Colours: the hex of a text role must be in the style guide. When only a background was measured, choose text, muted and line colours to meet contrast (4.5:1 for body text) and record them as chosen, not measured.

## Hard lines

- Take the grammar of the reference (structure, proportions, spacing, type scale, timing), never its text, logos, photographs or videos.
- These defaults are banned: a centred title over a soft gradient; a purple-to-blue gradient; everything fading in on load; a label above three icon cards; a card inside a card.
- Animate `transform` and `opacity` only, never width, height, top, left or margin.
- Respect `prefers-reduced-motion`: when it is set, use the reduced-motion fallback the recipe gives, or show the final state without movement.
- No animation may shift the layout.

## Verify

Run every check and report each one as passed or failed.

- The build passes.
- The browser console shows no error, on load and while scrolling the whole page.
- Take screenshots at 390, 768 and 1440 px wide and look at each one.
- There is no horizontal overflow at any of those widths.
- Say what you did not check. Never report a check you did not run.

## Deliver

Deliver the project files and a `README.md` that states what was verified and what was not checked.
