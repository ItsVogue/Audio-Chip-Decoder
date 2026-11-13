/* Minimal stand-in for zlib, for the Chip Audio Decoder build of minipro.
 * minipro uses zlib only to unpack T56/T76 algorithm files, which this build
 * does not ship. Inflate therefore reports an error, and those two programmers
 * are reported as unsupported; TL866A/CS, TL866II Plus and T48 need none of it. */
#ifndef CAD_ZLIB_SHIM_H
#define CAD_ZLIB_SHIM_H
#include <stddef.h>
typedef unsigned char Bytef;
typedef unsigned int uInt;
typedef unsigned long uLong;
typedef void *voidpf;
typedef struct z_stream_s {
	const Bytef *next_in; uInt avail_in; uLong total_in;
	Bytef *next_out; uInt avail_out; uLong total_out;
	const char *msg; void *state;
	voidpf (*zalloc)(voidpf, uInt, uInt); void (*zfree)(voidpf, voidpf); voidpf opaque;
	int data_type; uLong adler; uLong reserved;
} z_stream;
#define Z_OK 0
#define Z_STREAM_END 1
#define Z_FINISH 4
#define Z_DATA_ERROR (-3)
#define Z_NULL 0
#define MAX_WBITS 15
static inline int inflateInit2(z_stream *s, int bits) { (void)s; (void)bits; return Z_OK; }
static inline int inflate(z_stream *s, int flush) { (void)s; (void)flush; return Z_DATA_ERROR; }
static inline int inflateEnd(z_stream *s) { (void)s; return Z_OK; }
#endif
