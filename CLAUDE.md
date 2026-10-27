@AGENTS.md

## Claude Code

- **Hooks** (`.claude/settings.json`, scripts in `scripts/hooks/`): at session start you are shown the state of git, the errors the project memory still holds open, and `docs/map/` is regenerated; an edit of a generated file (`docs/map/`, `desktop/src/locales/it.ts`) is refused and one of a committed migration asks first; a Python or JSON file you just wrote that does not parse is reported to you at once; when you stop, the session is recorded in the project memory. They never block your work on their own failure.
- **Rules by path** are in `.claude/rules/`; the nested `AGENTS.md` files (imported by a `CLAUDE.md` next to each) load when you work in `arena/`, `imagegen/`, `workflows/`, `brain/` and `desktop/`.
- **Tools.** `codegraph_explore` (MCP, user level) or `codegraph explore` in a shell for symbols and blast radius; `willitrun memory recall` for decisions and past errors.
- Keep this file and `AGENTS.md` under 200 lines; put detail in `docs/` and let the map and the graph answer questions about code.
