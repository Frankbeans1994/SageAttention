"""
Copyright (c) 2024 by SageAttention team.

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.
"""

import os
import subprocess
from packaging.version import parse, Version
import warnings

from setuptools import setup, find_packages
import torch
from torch.utils.cpp_extension import BuildExtension, CUDAExtension, CUDA_HOME

# Compiler flags.
if os.name == "nt":
    # TODO: Detect MSVC rather than OS
    CXX_FLAGS = ["/O2", "/openmp", "/std:c++17", "-DENABLE_BF16"]
else:
    CXX_FLAGS = ["-g", "-O3", "-fopenmp", "-lgomp", "-std=c++17", "-DENABLE_BF16"]
NVCC_FLAGS_COMMON = [
    "-O3",
    "-std=c++17",
    "-U__CUDA_NO_HALF_OPERATORS__",
    "-U__CUDA_NO_HALF_CONVERSIONS__",
    "--use_fast_math",
    f"--threads={os.cpu_count()}",
    # "-Xptxas=-v",
    "-diag-suppress=174", # suppress the specific warning
    "-diag-suppress=177",
    "-diag-suppress=221",
]

ABI = 1 if torch._C._GLIBCXX_USE_CXX11_ABI else 0
CXX_FLAGS += [f"-D_GLIBCXX_USE_CXX11_ABI={ABI}"]
NVCC_FLAGS_COMMON += [f"-D_GLIBCXX_USE_CXX11_ABI={ABI}"]

if CUDA_HOME is None:
    raise RuntimeError(
        "Cannot find CUDA_HOME. CUDA must be available to build the package.")

def get_nvcc_cuda_version(cuda_dir: str) -> Version:
    """Get the CUDA version from nvcc.

    Adapted from https://github.com/NVIDIA/apex/blob/8b7a1ff183741dd8f9b87e7bafd04cfde99cea28/setup.py
    """
    nvcc_output = subprocess.check_output([cuda_dir + "/bin/nvcc", "-V"],
                                          universal_newlines=True)
    output = nvcc_output.split()
    release_idx = output.index("release") + 1
    nvcc_cuda_version = parse(output[release_idx].split(",")[0])
    return nvcc_cuda_version

compute_capabilities = set()
if os.getenv("SAGEATTENTION_CUDA_ARCH_LIST"):
    for x in os.getenv("SAGEATTENTION_CUDA_ARCH_LIST").split():
        compute_capabilities.add(x)
else:
    # Iterate over all GPUs on the current machine.
    device_count = torch.cuda.device_count()
    for i in range(device_count):
        major, minor = torch.cuda.get_device_capability(i)
        if major < 8:
            warnings.warn(f"skipping GPU {i} with compute capability {major}.{minor}")
            continue
        compute_capabilities.add(f"{major}.{minor}")

nvcc_cuda_version = get_nvcc_cuda_version(CUDA_HOME)
if not compute_capabilities:
    raise RuntimeError("No GPUs found. Please specify SAGEATTENTION_CUDA_ARCH_LIST or build on a machine with GPUs.")
else:
    print(f"Detected compute capabilities: {compute_capabilities}")

def has_capability(target):
    return any(cc.startswith(target) for cc in compute_capabilities)

# Validate the NVCC CUDA version.
if nvcc_cuda_version < Version("12.0"):
    raise RuntimeError("CUDA 12.0 or higher is required to build the package.")
if nvcc_cuda_version < Version("12.4") and has_capability("8.9"):
    raise RuntimeError(
        "CUDA 12.4 or higher is required for compute capability 8.9.")
if nvcc_cuda_version < Version("12.3") and has_capability("9.0"):
    raise RuntimeError(
        "CUDA 12.3 or higher is required for compute capability 9.0.")
if nvcc_cuda_version < Version("12.8") and has_capability("12.0"):
    raise RuntimeError(
        "CUDA 12.8 or higher is required for compute capability 12.0.")

# Add target compute capabilities to NVCC flags.
def get_nvcc_flags(allowed_capabilities):
    NVCC_FLAGS = []
    for capability in compute_capabilities:
        if capability not in allowed_capabilities:
            continue

        # capability: "8.0+PTX" -> num: "80"
        num = capability.split("+")[0].replace(".", "")
        if num in {"90", "120"}:
            # need to use sm90a instead of sm90 to use wgmma ptx instruction.
            # need to use sm120a to use mxfp8/mxfp4/nvfp4 instructions.
            num += "a"

        NVCC_FLAGS += ["-gencode", f"arch=compute_{num},code=sm_{num}"]
        if capability.endswith("+PTX"):
            NVCC_FLAGS += ["-gencode", f"arch=compute_{num},code=compute_{num}"]

    NVCC_FLAGS += NVCC_FLAGS_COMMON
    return NVCC_FLAGS

ext_modules = []

if has_capability(("8.0",)):
    qattn_extension = CUDAExtension(
        name="sageattention._qattn_sm80",
        sources=[
            "csrc/qattn/pybind_sm80.cpp",
            "csrc/qattn/qk_int_sv_f16_cuda_sm80.cu",
        ],
        extra_compile_args={
            "cxx": CXX_FLAGS,
            "nvcc": get_nvcc_flags(["8.0"]),
        },
    )
    ext_modules.append(qattn_extension)

if has_capability(("8.9", "12.0")):
    qattn_extension = CUDAExtension(
        name="sageattention._qattn_sm89",
        sources=[
            "csrc/qattn/pybind_sm89.cpp",
            "csrc/qattn/sm89_qk_int8_sv_f8_accum_f32_attn_inst_buf.cu",
            "csrc/qattn/sm89_qk_int8_sv_f8_accum_f16_attn_inst_buf.cu",
            "csrc/qattn/sm89_qk_int8_sv_f8_accum_f32_attn.cu",
            "csrc/qattn/sm89_qk_int8_sv_f8_accum_f32_fuse_v_scale_fuse_v_mean_attn.cu",
            "csrc/qattn/sm89_qk_int8_sv_f8_accum_f32_fuse_v_scale_attn.cu",
            "csrc/qattn/sm89_qk_int8_sv_f8_accum_f32_fuse_v_scale_attn_inst_buf.cu",
            "csrc/qattn/sm89_qk_int8_sv_f8_accum_f16_fuse_v_scale_attn_inst_buf.cu"
        ],
        extra_compile_args={
            "cxx": CXX_FLAGS,
            "nvcc": get_nvcc_flags(["8.9", "12.0"]),
        },
    )
    ext_modules.append(qattn_extension)

if has_capability(("9.0",)):
    qattn_extension = CUDAExtension(
        name="sageattention._qattn_sm90",
        sources=[
            "csrc/qattn/pybind_sm90.cpp",
            "csrc/qattn/qk_int_sv_f8_cuda_sm90.cu",
        ],
        libraries=["cuda"],
        extra_compile_args={
            "cxx": CXX_FLAGS,
            "nvcc": get_nvcc_flags(["9.0"]),
        },
    )
    ext_modules.append(qattn_extension)

# Fused kernels.
fused_extension = CUDAExtension(
    name="sageattention._fused",
    sources=["csrc/fused/pybind.cpp", "csrc/fused/fused.cu"],
    extra_compile_args={
        "cxx": CXX_FLAGS,
        "nvcc": get_nvcc_flags(["8.0", "8.9", "9.0", "12.0"]),
    },
)
ext_modules.append(fused_extension)

setup(
    name='sageattention',
    version='2.2.0' + os.environ.get("SAGEATTENTION_WHEEL_VERSION_SUFFIX", ""),
    author='SageAttention team',
    license='Apache 2.0 License',
    description='Accurate and efficient plug-and-play low-bit attention.',
    long_description=open('README.md', encoding='utf-8').read(),
    long_description_content_type='text/markdown',
    url='https://github.com/thu-ml/SageAttention',
    packages=find_packages(),
    python_requires='>=3.9',
    ext_modules=ext_modules,
    cmdclass={"build_ext": BuildExtension},
)
