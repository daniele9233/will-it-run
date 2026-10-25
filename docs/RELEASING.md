# Releasing

Every new version of Will It Run is published as a [GitHub release](https://github.com/daniele9233/will-it-run/releases)
with the installer and its SHA-256 checksum. Anyone can download the latest one from
<https://github.com/daniele9233/will-it-run/releases/latest>.

## One command

Commit the work first (the script refuses to start on a dirty tree, so the release commit holds only the version), then:

```powershell
.\scripts\release.ps1 -DryRun      # says the version, the notes and the files, changes nothing
.\scripts\release.ps1              # patch release: 0.1.0 -> 0.1.1
.\scripts\release.ps1 -Bump minor  # 0.1.0 -> 0.2.0 (or -Bump major, or -Version 1.2.3)
```

Run it in a Terminal tab, not through a pipe that merges stderr: PyInstaller's progress lines on stderr make
`build.ps1` fail in Windows PowerShell 5.1 when they are merged into the output.

What it does:

1. Checks the working tree is clean and `gh` is signed in; works out the next version from the newest `v*` tag.
2. Writes the version in the five places that hold it: `backend/pyproject.toml`,
   `backend/src/agentfit/branding.py`, `desktop/package.json` (and its lock file), `desktop/src-tauri/Cargo.toml` and
   `desktop/src-tauri/tauri.conf.json`.
3. Runs `scripts\build.ps1` (lint, tests, frozen core, installer) and checks that the installer carries the new version.
4. Copies the installer to `dist\release\WillItRun-<version>-Setup-x64.exe` and writes the `.sha256` beside it.
5. Commits `Release v<version>`, tags `v<version>`, pushes the branch and the tag.
6. Creates the GitHub release with the installer and the checksum. The notes are the subjects of the commits since the
   last release (or `-NotesFile notes.md`), followed by how to install and what the app needs.

`-CommitTrailer "Co-Authored-By: Name <mail>"` adds a line at the end of the release commit message.
`-SkipTests` is passed to `build.ps1`.

## Notes

- The installer is not code-signed: Windows SmartScreen warns on first run. The release notes say so.
- `dist\` is not in git: the installer exists only as a release asset.
- Releases are public. Do not publish a build that contains anything that is not meant to be public.
