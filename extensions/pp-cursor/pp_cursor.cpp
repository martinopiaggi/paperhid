// XOVI extension skeleton — composite mouse cursor at EPFramebuffer::swapBuffers.
// NOT production-ready: needs correct mangled symbol + xovi.h from asivery/xovi
// and a tested override for your libqsgepaper.so build.
//
// Build: aarch64-linux-gnu-g++ -shared -fPIC -O2 -o pp-cursor.so pp_cursor.cpp
//
// Protocol: /dev/shm/pp-cursor (see README.md)

#include <stdint.h>
#include <string.h>
#include <fcntl.h>
#include <unistd.h>
#include <sys/mman.h>
#include <stdio.h>

static const char *SHM_PATH = "/dev/shm/pp-cursor";
static const uint32_t MAGIC = 0x50504353u;

struct CursorShm {
    uint32_t magic;
    uint32_t version;
    int32_t x;
    int32_t y;
    uint32_t flags;
    uint32_t seq;
};

static CursorShm *g_cur = nullptr;

static void ensure_shm() {
    if (g_cur)
        return;
    int fd = open(SHM_PATH, O_RDONLY);
    if (fd < 0)
        return;
    void *p = mmap(nullptr, sizeof(CursorShm), PROT_READ, MAP_SHARED, fd, 0);
    close(fd);
    if (p == MAP_FAILED)
        return;
    g_cur = (CursorShm *)p;
}

// TODO: wire with XOVI override$ of
//   EPFramebuffer::swapBuffers(QRect, EPScreenMode, QFlags)
// After original call (or before), if g_cur valid && magic/version match && flags&1,
// draw a small cross into the framebuffer QImage covering (x,y).
// Prefer calling swapBuffers again for a 32x32 dirty rect if ABI allows.

extern "C" void _xovi_construct() {
    ensure_shm();
    fprintf(stderr, "pp-cursor: construct (stub — hook not linked)\n");
}
