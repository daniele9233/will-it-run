#!/usr/bin/env bash
set -euo pipefail
export HF_HUB_DISABLE_TELEMETRY=1 DO_NOT_TRACK=1 PIP_DISABLE_PIP_VERSION_CHECK=1
ROOT="$HOME/.local/share/willitrun-hi3d"
BASE="$HOME/.local/share/willitrun-trellis2/env"
mkdir -p "$ROOT"
if [ ! -d "$ROOT/repo/.git" ]; then
  git clone https://github.com/hi3d-official/Hi3Dv1.0.git "$ROOT/repo"
fi
git -C "$ROOT/repo" checkout --detach 13657ad09afaa92e350b461a1cd66399e44a59c4
if [ ! -f "$ROOT/env/bin/python" ]; then
  "$BASE/bin/python" -m venv --system-site-packages "$ROOT/env"
fi
export CUDA_HOME="$BASE" PATH="$BASE/bin:$PATH"
export LD_LIBRARY_PATH="$BASE/lib:${LD_LIBRARY_PATH:-}"
export CXX="$BASE/bin/x86_64-conda-linux-gnu-c++" CC="$BASE/bin/x86_64-conda-linux-gnu-cc"
export CPATH="$BASE/targets/x86_64-linux/include" LIBRARY_PATH="$BASE/lib:$BASE/targets/x86_64-linux/lib/stubs"
export MAX_JOBS=2
export TORCH_CUDA_ARCH_LIST="$("$BASE/bin/python" -c 'import torch; print("%s.%s" % torch.cuda.get_device_capability())')"
"$ROOT/env/bin/pip" install 'setuptools>=77.0.3' wheel packaging ninja psutil
"$ROOT/env/bin/pip" install 'transformers==4.46.3' 'trimesh==4.12.2' 'pyfqmr==0.5.0' 'accelerate==1.15.0' 'spconv-cu120==2.3.6'
"$ROOT/env/bin/pip" install --no-build-isolation 'flash-attn==2.7.4.post1'
"$ROOT/env/bin/pip" install --no-deps -e "$ROOT/repo"
"$ROOT/env/bin/python" -c 'import torch, flash_attn, spconv, sparc3d; assert torch.cuda.is_available()'
