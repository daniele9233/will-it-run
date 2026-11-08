# Releasing

Every new version of Will It Run is published as a [GitHub release](https://github.com/daniele9233/will-it-run/releases)
with the installer and its SHA-256 checksum. Anyone can download the latest one from
<https://github.com/daniele9233/will-it-run/releases/latest>.

## One command

### Installer builds on GitHub

Publishing a release also starts `.github/workflows/release.yml` on a clean
Windows runner. It checks that the tag matches the application version, runs
`python -X utf8 scripts/check.py --all`, builds the core and installer, then
attaches the versioned installer and SHA-256 file to that release. No personal
access token is needed. The installer is available only after this workflow
succeeds; a published tag alone is not proof that packaging completed.

This also supports publishing validated source through GitHub when a restricted
local Windows environment cannot run NSIS. The local command below remains
available; an existing release's assets are replaced only by a successful build
of that same tag.

If an existing release is missing its installer after a transient download or
packaging failure, retry its tag from the current workflow on master. For example:

```powershell
gh workflow run release.yml --ref master -f tag=v0.4.1
```

This packages the existing tag's source. Once the run succeeds, download both
assets, compare the installer's SHA-256 with the attached checksum and GitHub's
asset digest, and check its product version. This recovered the 0.4.1 installer
after the NSIS download returned HTTP 503; the published tag stayed unchanged.

### Local release command

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
- If the script stops after it wrote the version (a build step failed), nothing was committed, tagged or published, but the six version files are left modified. Look at `git status`, restore them with `git checkout -- <files>` (they are the only changes) and run it again.
- The Tauri step needs Rust; `build.ps1` looks for `cargo` on PATH, then in the cargo and rustup folders of the user, and stops at the start when it is nowhere.
- `dist\` is not in git: the installer exists only as a release asset.
- Releases are public. Do not publish a build that contains anything that is not meant to be public.
