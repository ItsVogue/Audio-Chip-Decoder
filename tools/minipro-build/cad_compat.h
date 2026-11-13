/* Functions this compiler's Windows C library lacks. */
#ifndef CAD_COMPAT_H
#define CAD_COMPAT_H
#include <string.h>
static inline char *cad_stpcpy(char *dst, const char *src)
{
	size_t n = strlen(src);
	memcpy(dst, src, n + 1);
	return dst + n;
}
#define stpcpy cad_stpcpy
#endif
