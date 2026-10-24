# Hardware detection

`willitrun hardware` and the Hardware page show the result. `backend/src/agentfit/hardware`.

## Probes

| What | Source | Notes |
|---|---|---|
| Windows version, build | `Win32_OperatingSystem` | |
| CPU model, cores, threads, frequency | `Win32_Processor` | Summed across sockets |
| SSE4.1, SSE4.2, AVX, AVX2, AVX-512F | `IsProcessorFeaturePresent` (kernel32) | `None` when the call is unavailable, never guessed |
| RAM visible / available | `Win32_OperatingSystem` | |
| RAM modules, type, speed | `Win32_PhysicalMemory` | DDR type from `SMBIOSMemoryType` |
| NVIDIA GPUs | `nvidia-smi --query-gpu=...` | Exact VRAM, live usage, driver, UUID |
| CUDA version | `nvidia-smi` banner | Handles both `CUDA Version:` and the 600-series `CUDA UMD Version:` |
| All display adapters | `Win32_VideoController` | AMD, Intel, and NVIDIA when nvidia-smi is missing |
| True VRAM | Registry `HardwareInformation.qwMemorySize` | See below |
| Drives | `Win32_LogicalDisk` (fixed) + `Get-Partition` / `Get-PhysicalDisk` | Media and bus type are best-effort |

All CIM and registry queries run in **one** PowerShell process that returns JSON
(`windows.PROBE_SCRIPT`), so a scan takes about 3 to 4 seconds. The parsers take plain
dictionaries, which is what lets the test suite feed them recorded fixtures.

## The 4 GB VRAM problem

`Win32_VideoController.AdapterRAM` is a 32-bit field. Every card with 4 GB or more
reports about 4 GB. Will It Run reads the 64-bit size the driver publishes in the registry
(`HKLM\SYSTEM\CurrentControlSet\Control\Class\{4d36e968-...}\*`) and matches it to the
adapter by name, normalizing trademark decoration (`AMD Radeon(TM) Graphics` and
`AMD Radeon Graphics` are the same adapter).

Each GPU carries a `vram_confidence`:

- **HIGH**: nvidia-smi or registry
- **MEDIUM**: WMI below the 4 GB cap
- **LOW**: WMI at the cap with no registry value; a warning is shown

## Merging and multi-GPU

NVIDIA cards come from nvidia-smi and are paired with their WMI entry by name to pick up
the driver date and shared-memory figure. AMD and Intel adapters come from WMI. Virtual
adapters (Basic Display, Remote Display, Parsec, ...) are dropped. Each GPU records the
probes that contributed (`sources`).

Integrated graphics are detected by name (Intel UHD/Iris and plain "Arc Graphics", AMD
"Radeon Graphics"/Vega/xxxM) and are **not** counted as inference GPUs. Their memory is
borrowed system RAM, so planning CPU inference is more predictable.

Several discrete GPUs are listed separately. Will It Run does not assume `24 + 24 = 48`:

- `single_gpu_usable_gb`: the largest single card, the limit for a runtime that cannot split
- `split_usable_gb`: the sum of usable VRAM across cards **with the same backend**, minus
  0.5 GB per extra card. This is what llama.cpp's layer split (the engine behind LM
  Studio's GGUF support) can use.
- Speed for a split model is bounded by the slowest card, with a 10% penalty.

## From detected memory to usable memory

Installed memory is never treated as available (`hardware/budget.py`):

```
GPU VRAM                      16.0 GB
- Windows / desktop reserve    1.0 GB     settings: hardware.vram_os_reserve_gb
- safety reserve (5%)          0.8 GB     settings: hardware.vram_reserve_percent
= recommended usable          14.2 GB

RAM visible to Windows        31.8 GB
- reserve (15%)                4.8 GB     settings: hardware.ram_reserve_percent
= recommended usable          27.0 GB
```

Only the GPU driving the display pays the full Windows reserve; further GPUs pay 0.3 GB.
Every line of this arithmetic is returned in `budget.explanation` and shown in the UI.

## Fallbacks and failure

If PowerShell or CIM fails, `psutil` supplies RAM and core counts, the profile is marked
`hardware_detection: LOW` and a warning explains why. Detection never raises: a machine
with no GPU is a valid result ("models will run on the CPU").

## Tested hardware shapes

`backend/tests/fixtures/hardware/` holds probe output for: RTX 3060 12 GB, RTX 4060 Ti
16 GB, RTX 4080 SUPER 16 GB, RTX 4090 24 GB, RTX 5090 32 GB, CPU-only 16 GB, dual
NVIDIA (4090 + 3090), AMD RX 7900 XTX with an integrated Radeon, and Intel Arc A770
with UHD graphics. Regenerate with `scripts/make_hardware_fixtures.py`.

The RTX 4080 SUPER / i7-14700K / 32 GB shape is also the machine Will It Run was developed
and verified on with live probes.
