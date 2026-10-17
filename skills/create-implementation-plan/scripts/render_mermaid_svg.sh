#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 1 ]]; then
  echo "usage: $0 <path/to/diagram.mmd>" >&2
  exit 2
fi

INPUT="$1"
if [[ ! -f "$INPUT" ]]; then
  echo "mermaid file not found: $INPUT" >&2
  exit 2
fi

OUTPUT="${INPUT%.*}.svg"

if command -v mmdc >/dev/null 2>&1; then
  mmdc -i "$INPUT" -o "$OUTPUT"
  echo "wrote: $OUTPUT" >&2
  exit 0
fi

if command -v npx >/dev/null 2>&1; then
  set +e
  npx --yes @mermaid-js/mermaid-cli -i "$INPUT" -o "$OUTPUT"
  status=$?
  if [[ $status -eq 0 ]]; then
    echo "wrote: $OUTPUT" >&2
    exit 0
  fi

  npx -y @mermaid-js/mermaid-cli -i "$INPUT" -o "$OUTPUT"
  status=$?
  set -e

  if [[ $status -eq 0 ]]; then
    echo "wrote: $OUTPUT" >&2
    exit 0
  fi
fi

if command -v docker >/dev/null 2>&1; then
  DIR="$(cd "$(dirname "$INPUT")" && pwd)"
  BASE="$(basename "$INPUT")"
  OUT_BASE="$(basename "$OUTPUT")"

  set +e
  docker run --rm -v "$DIR:/data" ghcr.io/mermaid-js/mermaid-cli/mermaid-cli -i "/data/$BASE" -o "/data/$OUT_BASE"
  status=$?
  if [[ $status -eq 0 ]]; then
    echo "wrote: $OUTPUT" >&2
    exit 0
  fi

  docker run --rm -v "$DIR:/data" minlag/mermaid-cli -i "/data/$BASE" -o "/data/$OUT_BASE"
  status=$?
  set -e

  if [[ $status -eq 0 ]]; then
    echo "wrote: $OUTPUT" >&2
    exit 0
  fi
fi

echo "mermaid-cli (mmdc) not found; couldn't render svg from: $INPUT" >&2
echo "install mermaid-cli (mmdc), or ensure npx/docker is available" >&2
exit 1
