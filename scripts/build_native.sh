#!/usr/bin/env bash
# Build the optional Rust taxi-loop extension (native/taxi) and copy it to
# backend/wave/_taxi_native.abi3.so. Without cargo this exits 0 and the engine
# runs its pure-Python loop.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

export PATH="$HOME/.cargo/bin:$PATH"
if ! command -v cargo >/dev/null 2>&1; then
  echo "cargo not found: skipping the Rust taxi extension (the engine falls back to Python)"
  exit 0
fi

case "$(uname -s)" in
  Darwin) LIB=lib_taxi_native.dylib ;;
  Linux) LIB=lib_taxi_native.so ;;
  *)
    echo "unsupported platform $(uname -s): skipping the Rust taxi extension"
    exit 0
    ;;
esac

(cd native/taxi && cargo build --release)

DST="$ROOT/backend/wave/_taxi_native.abi3.so"
# copy then rename: a running interpreter keeps the old file it mapped (cp over it in place could crash it)
trap 'rm -f "$DST.tmp"' EXIT   # a failed cp leaves no .tmp behind (.gitignore / .dockerignore do not cover it)
cp "$ROOT/native/taxi/target/release/$LIB" "$DST.tmp"
mv -f "$DST.tmp" "$DST"
echo "wrote $DST"
