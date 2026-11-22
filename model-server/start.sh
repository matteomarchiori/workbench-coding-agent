#!/bin/bash
set -e

cd "$(dirname "$0")"

exec .venv/bin/python -m mlx_lm.server \
  --model Qwen/Qwen3-8B-MLX-4bit \
  --host 0.0.0.0 \
  --port 8000
