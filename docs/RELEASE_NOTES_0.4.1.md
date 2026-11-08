# Will It Run 0.4.1

- Remove the Voice interface, runtime/API integration and automatic Voice setup.
- Remove only Qwen 3D Coder from the 3D studio, preserving TRELLIS.2, Hi3D,
  comparison engines, gallery, preview and Web GLB export.
- Show video generation progress, actual sampling steps, phase and elapsed time,
  including decoding, saving and Full HD finishing. Progress belongs only to
  the submitted video; an unavailable progress socket never stops generation.
- Keep remaining time explicitly unknown when it has not been measured; the
  progress bar reaches 100% only after the result is saved.
- Update project decisions, lessons and studio documentation.

Model files, generated pictures, 3D assets and private application data are not
included in the installer or public repository. Existing 3D files are preserved.
