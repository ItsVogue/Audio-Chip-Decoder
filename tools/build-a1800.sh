#!/bin/sh
# Builds audiochip/a18helper/a1800.exe: the open-source GeneralPlus A1800 codec
# (https://github.com/John-K/a1800_codec, Apache License 2.0) as a Windows program.
# Needs Rust with the x86_64-pc-windows-gnu target and mingw-w64.
#   rustup target add x86_64-pc-windows-gnu
# Usage: tools/build-a1800.sh        Output: build/a1800/a1800.exe
set -eu
COMMIT=c32c383e2285825eca3f2a0cf4f41d607392ab24
ROOT=$(cd "$(dirname "$0")/.." && pwd)
WORK="$ROOT/build/a1800"
mkdir -p "$WORK"
if [ ! -d "$WORK/src" ]; then
  git clone --quiet https://github.com/John-K/a1800_codec "$WORK/src"
fi
cd "$WORK/src"
git checkout --quiet "$COMMIT"
RUSTFLAGS="--remap-path-prefix=$WORK/src=a1800_codec --remap-path-prefix=$HOME/.cargo=cargo --remap-path-prefix=$HOME/.rustup=rustup -C strip=symbols -C target-feature=+crt-static" \
  cargo build --release --target x86_64-pc-windows-gnu \
  --config 'target.x86_64-pc-windows-gnu.linker="x86_64-w64-mingw32-gcc"'
cp target/x86_64-pc-windows-gnu/release/a1800_codec.exe "$WORK/a1800.exe"
ls -l "$WORK/a1800.exe"
