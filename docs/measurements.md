# Measurements

Real numbers from this laptop. Every table records the date and how it was measured.

## Host (step 0.1)

Measured 2026-10-18.

| Item | Value | Source |
| --- | --- | --- |
| Laptop | HP Victus 16, Ryzen 7 5800H, 32 GB RAM (30,907 MiB visible) | `free -m` |
| GPU | NVIDIA GeForce RTX 3060 Laptop (GA106M), 6,144 MiB VRAM | `nvidia-smi`, `lspci` |
| iGPU | AMD Cezanne (Radeon Vega) | `lspci` |
| PRIME mode | on-demand | `prime-select query` |
| NVIDIA driver | 595.99.02 | `nvidia-smi` |
| Max CUDA supported by driver | 13.2 (so llama-swap tag `unified-cuda13` in step 0.4) | `nvidia-smi` |
| Docker Engine | 29.9.1 | `docker version` |
| Docker Compose | v5.6.0 (Compose v2 line, plugin) | `docker compose version` |
| NVIDIA Container Toolkit | 1.20.1 (apt repo nvidia.github.io/libnvidia-container), Docker runtime configured, CDI spec in `/etc/cdi/nvidia.yaml` | `nvidia-ctk --version`, apt |
| GPU visible in Docker | Yes: RTX 3060 Laptop, 6,144 MiB, driver 595.99.02, CUDA 13.2 | `docker run --rm --gpus all ubuntu:24.04 nvidia-smi` |
| Free disk on `/` (holds `/var/lib/docker`) | 351 GB of 468 GB | `df -h` |
| Host RAM available at idle (desktop session open) | 10,916 MiB, swap in use 1,545 MiB | `free -m` |

## Benchmark (step 0.5)

| Date | Model | Prompt | Cold/warm | TTFT (s) | Decode tok/s | Prompt tok/s (server) | Cached tokens | VRAM used (MiB) | `llm` RSS (MiB) | Host free (MiB) |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |

## Simulation and RAM under load (step 0.7)

| Date | Run | Status | Duration (s) | Result file size | Peak host RAM (MiB) | Notes |
| --- | --- | --- | --- | --- | --- | --- |
