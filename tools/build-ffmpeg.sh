#!/bin/sh
# Builds the small FFmpeg that Audio Chip Decoder ships as audiochip/ffhelper/ffmpeg.exe.
#
# It is FFmpeg with everything switched off except the audio decoders and file readers
# listed below, so it is a few megabytes instead of a hundred, and it is LGPL 2.1 or later
# (no GPL or non-free parts are enabled). The app runs it as a separate program.
#
# Usage:   tools/build-ffmpeg.sh win      Windows 64-bit exe, cross-compiled (needs mingw-w64 and nasm)
#          tools/build-ffmpeg.sh native   for the machine this runs on (used by the tests on Linux)
# Output:  build/ffmpeg/<target>/ffmpeg[.exe]
set -eu

VERSION=n8.1.3
TARGET=${1:-win}
ROOT=$(cd "$(dirname "$0")/.." && pwd)
WORK="$ROOT/build/ffmpeg"
SRC="$WORK/src-$VERSION"

DECODERS="aac aac_latm wmav1 wmav2 wmapro wmavoice wmalossless amrnb amrwb speex qcelp evrc
 adpcm_adx adpcm_thp adpcm_thp_le
 adpcm_ea adpcm_ea_r1 adpcm_ea_r2 adpcm_ea_r3 adpcm_ea_xas adpcm_ea_maxis_xa adpcm_ima_ea_sead adpcm_ima_ea_eacs
 binkaudio_rdft binkaudio_dct
 adpcm_ct adpcm_sbpro_2 adpcm_sbpro_3 adpcm_sbpro_4
 adpcm_g726 adpcm_g726le adpcm_ms adpcm_ima_wav adpcm_ima_qt adpcm_yamaha adpcm_ima_oki adpcm_swf
 truespeech g723_1 g729 gsm gsm_ms alac mp1 mp2 mp3
 pcm_s16le pcm_s16be pcm_u8 pcm_s8 pcm_s24le pcm_s24be pcm_s32le pcm_f32le pcm_alaw pcm_mulaw
 pcm_s16le_planar pcm_s16be_planar pcm_s8_planar"
DEMUXERS="aac mov asf amr amrnb amrwb adx bink binka ea voc qcp ogg wav caf xwma brstm bfstm genh g726 g726le"
PARSERS="aac aac_latm amr adx mpegaudio g729 g723_1"

join() { echo "$@" | tr -s ' \n' ',' | sed 's/^,//; s/,$//'; }

mkdir -p "$WORK"
if [ ! -d "$SRC" ]; then
  git clone --quiet --depth 1 --branch "$VERSION" https://github.com/FFmpeg/FFmpeg "$SRC"
fi

case "$TARGET" in
  win)    CROSS="--arch=x86_64 --target-os=mingw32 --cross-prefix=x86_64-w64-mingw32- --enable-w32threads --disable-pthreads --extra-ldflags=-static"; EXE=ffmpeg.exe ;;
  native) CROSS=""; EXE=ffmpeg ;;
  *) echo "target must be win or native" >&2; exit 2 ;;
esac

OUT="$WORK/$TARGET"
rm -rf "$OUT" && mkdir -p "$OUT"
cd "$OUT"
# shellcheck disable=SC2086
"$SRC/configure" $CROSS \
  --disable-everything --disable-autodetect --disable-doc --disable-debug --enable-small \
  --disable-ffplay --disable-ffprobe --disable-network --disable-avdevice --disable-swscale \
  --enable-decoder="$(join $DECODERS)" \
  --enable-demuxer="$(join $DEMUXERS)" \
  --enable-parser="$(join $PARSERS)" \
  --enable-protocol=file,pipe \
  --enable-muxer=wav,null --enable-encoder=pcm_s16le \
  --enable-filter=aresample,aformat,anull > configure.log
grep -i "^License" configure.log
make -j"$(nproc)" "$EXE" > make.log 2>&1 || { tail -30 make.log; exit 1; }
ls -l "$OUT/$EXE"
