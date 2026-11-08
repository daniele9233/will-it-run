# Will It Run 0.4.1

This release retires Voice and Qwen 3D Coder and adds observed video progress.

- Remove the Voice interface, runtime/API integration and automatic Voice setup.
- Remove only Qwen 3D Coder from the 3D studio, preserving TRELLIS.2, Hi3D,
  comparison engines, gallery, preview and Web GLB export.
- Show video generation progress, actual sampling steps, phase and elapsed time,
  including decoding, saving and Full HD finishing. Progress belongs only to
  the submitted video; an unavailable progress socket never stops generation.
- Keep remaining time explicitly unknown when it has not been measured; the
  progress bar reaches 100% only after the result is saved.
- Update project decisions, lessons and studio documentation.

## Windows installer

- [Download Will It Run 0.4.1 for Windows x64](https://github.com/daniele9233/will-it-run/releases/download/v0.4.1/WillItRun-0.4.1-Setup-x64.exe)
- [SHA-256 checksum](https://github.com/daniele9233/will-it-run/releases/download/v0.4.1/WillItRun-0.4.1-Setup-x64.exe.sha256)

The installer is not code-signed. Windows SmartScreen may ask for confirmation.

## Verification

The complete project checks passed locally and in the
[Windows release workflow](https://github.com/daniele9233/will-it-run/actions/runs/42473799795):
backend tests, Ruff, import contracts, TypeScript, ESLint, 158 frontend tests,
Italian translations and generated maps. The downloaded installer matches both
the attached SHA-256 checksum and GitHub's asset digest. An upgrade from the
maintainer's installed 0.4.0 completed successfully; desktop and frozen core both
report 0.4.1 and start successfully. Real GPU generation was not repeated.

The requested cleanup on the maintainer's PC was verified separately: only the
dedicated VoxCPM2 and dots.tts SOAR model folders were removed (24 files,
10,125,641,076 bytes). The installer does not perform this machine-specific cleanup.

Model files, generated pictures, 3D assets and private application data are not
included in the installer or public repository. Existing 3D files are preserved.
