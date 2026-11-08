#!/usr/bin/env bash
# Прогоняет демо-цикл на OfflineLLM и пишет docs/demo-transcript.md. Сеть и ключи не нужны.
set -euo pipefail
cd "$(dirname "$0")/.."
exec uv run python scripts/demo_transcript.py
