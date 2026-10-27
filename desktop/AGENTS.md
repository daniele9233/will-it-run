# Desktop (`desktop/`)

The interface: React + TypeScript (strict, `noUncheckedIndexedAccess`) in `src/`, and a tiny Tauri shell in `src-tauri/` that starts and stops the Python core. Read `docs/DESIGN.md`, `docs/SCREENS.md`, `docs/LOCALIZATION.md`, `PRODUCT.md`; `docs/map/frontend.md` lists every UI file, the API calls it makes and the Rust shell.

## Layout

`src/pages/` one file per screen (`Arena.tsx`, `Images.tsx`, `Brain.tsx`, `Workflows.tsx`, ...; the Arena's parts are in `pages/arena/`), `src/components/` the shell and the primitives (`ui.tsx`), `src/lib/` the API client (`api.ts`), the types (`types.ts`, which mirror the Pydantic models), the store, the formatting, and the logic that has tests (`*.test.ts`). `src/styles.css` holds the design tokens (OKLCH).

## Rules

- **Every visible string is `t("English")`** with a line `English ||| Italiano` in `src/locales/it.txt`; then `python -X utf8 scripts/build_locale.py` (from the repository root) lists missing keys, checks placeholders and writes `src/locales/it.ts`, which is never edited by hand. `lib/i18n.test.ts` fails on a missing or broken translation.
- **Talk to the core only through `lib/api.ts`**; add the type to `lib/types.ts` next to the endpoint. The core is at `127.0.0.1` with a per-launch bearer token. Browser development uses `VITE_AGENTFIT_API` and `VITE_AGENTFIT_TOKEN`.
- **No fabricated values**: an unknown number is shown as "Unknown" or "Not measured". Explain automatic choices with the reasons the core returns.
- **Logic goes in `lib/` with a test**; a page stays layout and wiring. Keep the Rust side minimal: no product logic in `src-tauri`.
- Use the tokens and primitives of `styles.css` and `components/ui.tsx`; status colors are semantic (green perfect, accent active, yellow warning, orange limited, red error, gray unavailable).

## Commands

`npm run dev` (http://127.0.0.1:1420, expects the core on 8765), `npm run typecheck`, `npm run lint`, `npm test` (vitest), `npx tauri dev` for the real shell. To try a screen without a GPU, `scripts/dev/lmstudio_stub.py` and `scripts/dev/core_with_stub.py` (see `docs/DEVELOPMENT.md`).
