#!/usr/bin/env bash
# Isolated TRELLIS.2 runtime. No system packages or changes to ComfyUI.
set -euo pipefail
ROOT="$HOME/.local/share/willitrun-trellis2"
mkdir -p "$ROOT"
cd "$ROOT"
rm -f "$ROOT/ready" "$ROOT/weights-ready"
export HF_HUB_DISABLE_TELEMETRY=1 DO_NOT_TRACK=1
if [ ! -x bin/micromamba ]; then
  mkdir -p bin
  if [ -x "$HOME/.local/share/willitrun-trellis/bin/micromamba" ]; then
    cp "$HOME/.local/share/willitrun-trellis/bin/micromamba" bin/micromamba
  else
    curl --fail --location https://micro.mamba.pm/api/micromamba/linux-64/latest -o micromamba.tar.bz2
    python3 -c 'import tarfile; tarfile.open("micromamba.tar.bz2").extract("bin/micromamba", ".")'
  fi
fi
export MAMBA_ROOT_PREFIX="$ROOT/mamba"
if [ ! -x env/bin/python ]; then
  bin/micromamba create -y -p "$ROOT/env" -c conda-forge -c nvidia python=3.10 pip 'cuda-toolkit=12.4' 'gxx_linux-64=12' git ninja libjpeg-turbo libusb
fi
export PATH="$ROOT/env/bin:$PATH" CUDA_HOME="$ROOT/env" MAX_JOBS=2
export CC="$ROOT/env/bin/x86_64-conda-linux-gnu-cc" CXX="$ROOT/env/bin/x86_64-conda-linux-gnu-c++"
export LD_LIBRARY_PATH="$ROOT/env/lib:${LD_LIBRARY_PATH:-}"
export CPATH="$ROOT/env/targets/x86_64-linux/include:${CPATH:-}"
export LIBRARY_PATH="$ROOT/env/lib:$ROOT/env/targets/x86_64-linux/lib/stubs:${LIBRARY_PATH:-}"
if [ ! -d repo/.git ]; then
  git clone --recurse-submodules https://github.com/microsoft/TRELLIS.2.git repo
fi
cd repo
git checkout --detach 75fbf0183001ed9876c8dbb35de6b68552ee08bd
git submodule update --init --recursive
git rev-parse HEAD > "$ROOT/revision.txt"
python -m pip install 'pip<26' setuptools wheel
python -m pip install torch==2.6.0 torchvision==0.21.0 xformers==0.0.29.post3 --index-url https://download.pytorch.org/whl/cu124 --extra-index-url https://pypi.org/simple
export TORCH_CUDA_ARCH_LIST="$(python -c 'import torch; a,b=torch.cuda.get_device_capability(); print(f"{a}.{b}")')"
python -m pip install 'numpy<2' 'plyfile<1.1.3' 'transformers==4.57.6' 'huggingface-hub<1' pillow imageio imageio-ffmpeg tqdm easydict opencv-python-headless scipy ninja trimesh zstandard kornia 'timm==1.0.22' 'rembg<2.0.70' onnxruntime tensorboard pandas lpips
python -m pip install git+https://github.com/EasternJournalist/utils3d.git@9a4eb15e4021b67b12c460c7057d642626897ec8
for spec in 'https://github.com/NVlabs/nvdiffrast.git nvdiffrast v0.4.0 253ac4fcea7de5f396371124af597e6cc957bfae' 'https://github.com/JeffreyXiang/nvdiffrec.git nvdiffrec renderutils b296927cc7fd01c2ac1087c8065c4d7248f72da4' 'https://github.com/JeffreyXiang/CuMesh.git CuMesh main 12289e1062f0603f2f0d0771b02e1395d247f26f' 'https://github.com/JeffreyXiang/FlexGEMM.git FlexGEMM main 6dd94a859c26ee8246888502eada3dd8ad85532e'; do
  read -r url name branch revision <<< "$spec"
  if [ ! -d "$ROOT/$name/.git" ]; then git clone -b "$branch" --recurse-submodules "$url" "$ROOT/$name"; fi
  git -C "$ROOT/$name" checkout --detach "$revision"
  git -C "$ROOT/$name" submodule update --init --recursive
  python -m pip install --no-build-isolation "$ROOT/$name"
done
python -m pip install --no-build-isolation ./o-voxel
ATTN_BACKEND=xformers python -c 'import torch, cumesh, o_voxel, flex_gemm; from trellis2.pipelines import Trellis2ImageTo3DPipeline; assert torch.cuda.is_available(); print(torch.cuda.get_device_name())'
touch "$ROOT/ready"
