/* pp-cursor: high-rate software cursor for Paper Pro (XOVI).
 *
 * Present path:
 *  1) Hook EPFramebuffer::swapBuffers (passthrough — never mutate args)
 *  2) Stamp crosshair into the live FB around each present
 *  3) 20 Hz poll: QWindowSystemInterface::handleExposeEvent(Async) with a
 *     small QRegion around the pointer so Qt schedules more presents even
 *     when the user is only moving the pad (no dangling QRegion* replay)
 *
 * Crashes avoided:
 *  - No QRect-arg rewrite on the QRegion overload
 *  - No replaying saved region pointers from a worker thread
 *  - Expose is posted async onto the GUI thread by Qt
 */
#define _GNU_SOURCE
#include <stdint.h>
#include <string.h>
#include <stdio.h>
#include <stdlib.h>
#include <unistd.h>
#include <fcntl.h>
#include <dlfcn.h>
#include <sys/mman.h>
#include <pthread.h>
#include <errno.h>
#include <time.h>
#include <stdatomic.h>

#include "types.h"

#define SHM_PATH "/dev/shm/pp-cursor"
#define SHM_MAGIC 0x50504353u
#define CURSOR_R 14
#define TRAMP_SIZE 16
#define FB_CACHE "/home/root/.paperpointer/fb.cfg"
#define SPY_PATH "/home/root/xovi/extensions.d/framebuffer-spy.so"
#define XOCHITL_PATH "/usr/bin/xochitl"
#define MARKER "swapBuffers:"
/* ~20 Hz force-present while cursor is visible */
#define POLL_NS 50000000L
#define EXPOSE_PAD 48

typedef struct {
    uint32_t magic, version;
    int32_t x, y;
    uint32_t flags, seq;
} CursorShm;

/* Qt6 QRegion is a single pointer-sized d-pointer on all platforms we care about. */
typedef struct {
    void *d;
} QRegionPod;

typedef struct {
    int x, y;
} QPointPod;

typedef FramebufferConfig (*fn_get_fb)(void);
typedef void (*fn_swap_raw)(void *self, uint64_t a1, uint64_t a2, uint64_t a3, uint64_t a4);

typedef void (*fn_qregion_ctor_iiii)(void *self, int x, int y, int w, int h, int type);
typedef void (*fn_qregion_dtor)(void *self);
typedef void *(*fn_focus_window)(void);
typedef void *(*fn_toplevel_at)(const QPointPod *pt);
typedef int (*fn_expose_async)(void *window, const void *region);

static fn_get_fb g_get_fb_real = NULL;
static fn_swap_raw g_swap_orig = NULL;
static volatile int g_run = 0;
static atomic_int g_hooked = 0;
static atomic_int g_install_once = 0;
static atomic_int g_depth = 0;

static fn_qregion_ctor_iiii g_region_ctor = NULL;
static fn_qregion_dtor g_region_dtor = NULL;
static fn_focus_window g_focus_window = NULL;
static fn_toplevel_at g_toplevel_at = NULL;
static fn_expose_async g_expose_async = NULL;

static atomic_uintptr_t g_window = 0;
static atomic_int g_disp_w = 1620;
static atomic_int g_disp_h = 2160;

static void *sym(void *h, const char *name) {
    void *p = h ? dlsym(h, name) : NULL;
    if (!p)
        p = dlsym(RTLD_DEFAULT, name);
    return p;
}

static void resolve_qt(void) {
    void *gui = dlopen("libQt6Gui.so.6", RTLD_NOW | RTLD_NOLOAD);
    if (!gui)
        gui = dlopen("libQt6Gui.so.6", RTLD_NOW);
    g_region_ctor = (fn_qregion_ctor_iiii)sym(
        gui, "_ZN7QRegionC1EiiiiNS_10RegionTypeE");
    g_region_dtor = (fn_qregion_dtor)sym(gui, "_ZN7QRegionD1Ev");
    g_focus_window = (fn_focus_window)sym(gui, "_ZN15QGuiApplication11focusWindowEv");
    g_toplevel_at = (fn_toplevel_at)sym(gui, "_ZN15QGuiApplication10topLevelAtERK6QPoint");
    g_expose_async = (fn_expose_async)sym(
        gui,
        "_ZN22QWindowSystemInterface17handleExposeEventINS_20AsynchronousDeliveryEEEbP7"
        "QWindowRK7QRegion");
    fprintf(stderr,
            "pp-cursor: qt region_ctor=%p dtor=%p focus=%p topAt=%p expose=%p\n",
            (void *)g_region_ctor, (void *)g_region_dtor, (void *)g_focus_window,
            (void *)g_toplevel_at, (void *)g_expose_async);
}

static FramebufferConfig get_fb(void) {
    if (g_get_fb_real) {
        FramebufferConfig c = g_get_fb_real();
        if (c.framebufferAddress) {
            if (c.width > 0)
                atomic_store(&g_disp_w, c.width);
            if (c.height > 0)
                atomic_store(&g_disp_h, c.height);
            return c;
        }
    }
    FramebufferConfig cfg;
    memset(&cfg, 0, sizeof(cfg));
    FILE *f = fopen(FB_CACHE, "r");
    if (!f)
        return cfg;
    char line[160];
    if (fgets(line, sizeof(line), f)) {
        char *p = line;
        if (p[0] == '0' && (p[1] == 'x' || p[1] == 'X')) {
            unsigned long long addr = strtoull(p + 2, &p, 16);
            int vals[5] = {0};
            int n = 0;
            while (*p && n < 5) {
                if (*p == ',') {
                    p++;
                    vals[n++] = (int)strtol(p, &p, 10);
                } else
                    p++;
            }
            if (n >= 4 && addr) {
                cfg.framebufferAddress = (void *)(uintptr_t)addr;
                cfg.width = vals[0];
                cfg.height = vals[1];
                cfg.type = vals[2];
                cfg.bpl = vals[3];
                cfg.requiresReload = n >= 5 ? (bool)vals[4] : false;
                atomic_store(&g_disp_w, cfg.width);
                atomic_store(&g_disp_h, cfg.height);
            }
        }
    }
    fclose(f);
    return cfg;
}

static int read_cursor(CursorShm *out) {
    int fd = open(SHM_PATH, O_RDONLY | O_CLOEXEC);
    if (fd < 0)
        return -1;
    ssize_t n = read(fd, out, sizeof(*out));
    close(fd);
    if (n < (ssize_t)sizeof(*out))
        return -1;
    if (out->magic != SHM_MAGIC || out->version != 1)
        return -1;
    return 0;
}

static inline void put_px(uint8_t *fb, int bpl, int w, int h, int x, int y,
                          uint8_t r, uint8_t g, uint8_t b) {
    if ((unsigned)x >= (unsigned)w || (unsigned)y >= (unsigned)h)
        return;
    uint8_t *p = fb + (size_t)y * (size_t)bpl + (size_t)x * 4u;
    p[0] = r;
    p[1] = g;
    p[2] = b;
    p[3] = 255;
}

static void draw_cross(uint8_t *fb, int bpl, int w, int h, int cx, int cy) {
    const int t = 2, arm = CURSOR_R;
    for (int d = -arm; d <= arm; d++) {
        for (int k = -(t + 2); k <= (t + 2); k++) {
            put_px(fb, bpl, w, h, cx + d, cy + k, 255, 255, 255);
            put_px(fb, bpl, w, h, cx + k, cy + d, 255, 255, 255);
        }
    }
    for (int d = -arm; d <= arm; d++) {
        for (int k = -t; k <= t; k++) {
            put_px(fb, bpl, w, h, cx + d, cy + k, 0, 0, 0);
            put_px(fb, bpl, w, h, cx + k, cy + d, 0, 0, 0);
        }
    }
    put_px(fb, bpl, w, h, cx, cy, 0, 0, 0);
}

static int clampi(int v, int lo, int hi) {
    if (v < lo)
        return lo;
    if (v > hi)
        return hi;
    return v;
}

/* Stamp cursor; returns 1 if visible. Fills cx/cy when non-NULL. */
static int composite_cursor(int *out_cx, int *out_cy) {
    CursorShm c;
    if (read_cursor(&c) != 0 || !(c.flags & 1u))
        return 0;
    FramebufferConfig cfg = get_fb();
    if (!cfg.framebufferAddress || cfg.type != FBSPY_TYPE_RGBA || cfg.width <= 0)
        return 0;
    int cx = clampi(c.x, 0, cfg.width - 1);
    int cy = clampi(c.y, 0, cfg.height - 1);
    draw_cross((uint8_t *)cfg.framebufferAddress, cfg.bpl, cfg.width, cfg.height, cx, cy);
    if (out_cx)
        *out_cx = cx;
    if (out_cy)
        *out_cy = cy;
    return 1;
}

static void *current_window(int cx, int cy) {
    void *w = (void *)atomic_load(&g_window);
    if (w)
        return w;
    if (g_focus_window) {
        w = g_focus_window();
        if (w) {
            atomic_store(&g_window, (uintptr_t)w);
            return w;
        }
    }
    if (g_toplevel_at) {
        QPointPod pt = {cx, cy};
        w = g_toplevel_at(&pt);
        if (w)
            atomic_store(&g_window, (uintptr_t)w);
        return w;
    }
    return NULL;
}

/* Ask Qt (async) to expose a rect so swapBuffers runs again soon. */
static int force_expose_rect(int x, int y, int w, int h) {
    if (!g_region_ctor || !g_region_dtor || !g_expose_async)
        return 0;
    int dw = atomic_load(&g_disp_w);
    int dh = atomic_load(&g_disp_h);
    if (w < 1)
        w = 1;
    if (h < 1)
        h = 1;
    if (x < 0)
        x = 0;
    if (y < 0)
        y = 0;
    if (x >= dw)
        x = dw - 1;
    if (y >= dh)
        y = dh - 1;
    if (x + w > dw)
        w = dw - x;
    if (y + h > dh)
        h = dh - y;

    void *win = current_window(x + w / 2, y + h / 2);
    if (!win)
        return 0;

    /* Oversize storage — Qt6 QRegion is a d-pointer; leave headroom. */
    unsigned char storage[64];
    memset(storage, 0, sizeof(storage));
    /* RegionType::Rectangle = 0 */
    g_region_ctor(storage, x, y, w, h, 0);
    int ok = g_expose_async(win, storage);
    g_region_dtor(storage);
    return ok;
}

static void force_cursor_present(int cx, int cy) {
    int pad = EXPOSE_PAD;
    force_expose_rect(cx - pad, cy - pad, pad * 2, pad * 2);
}

/*
 * aarch64 SysV: x0=this, x1..x4 remaining. Never mutate args.
 */
void hook_entry(void *self, uint64_t a1, uint64_t a2, uint64_t a3, uint64_t a4) {
    (void)self;
    if (!g_swap_orig)
        return;

    if (atomic_fetch_add(&g_depth, 1) > 0) {
        g_swap_orig(self, a1, a2, a3, a4);
        atomic_fetch_sub(&g_depth, 1);
        return;
    }

    int cx = 0, cy = 0;
    int has = composite_cursor(&cx, &cy);

    /* UI compose + present */
    g_swap_orig(self, a1, a2, a3, a4);

    /* Scene may have wiped the stamp — put it back and re-present while args live */
    has = composite_cursor(&cx, &cy) || has;
    if (has) {
        g_swap_orig(self, a1, a2, a3, a4);
        /* One more stamp so FB matches what we just pushed if present was partial */
        composite_cursor(&cx, &cy);
    }

    atomic_fetch_sub(&g_depth, 1);
}

static void write_branch(void *at, void *to) {
    uintptr_t imm = (uintptr_t)to;
    uint32_t *p = (uint32_t *)at;
    p[0] = 0xD2800010u | ((uint32_t)(imm & 0xFFFF) << 5);
    p[1] = 0xF2A00010u | ((uint32_t)((imm >> 16) & 0xFFFF) << 5);
    p[2] = 0xF2C00010u | ((uint32_t)((imm >> 32) & 0xFFFF) << 5);
    p[3] = 0xD61F0200u;
}

static void *make_orig_stub(void *func, const uint8_t orig[TRAMP_SIZE]) {
    void *page = mmap(NULL, 0x1000, PROT_READ | PROT_WRITE | PROT_EXEC,
                      MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
    if (page == MAP_FAILED)
        return NULL;
    memcpy(page, orig, TRAMP_SIZE);
    write_branch((uint8_t *)page + TRAMP_SIZE, (uint8_t *)func + TRAMP_SIZE);
    __builtin___clear_cache((char *)page, (char *)page + 32);
    return page;
}

static int install_hook(void *func, void *hook) {
    uint8_t orig[TRAMP_SIZE];
    memcpy(orig, func, TRAMP_SIZE);
    uintptr_t page = (uintptr_t)func & ~((uintptr_t)0xFFF);
    if (mprotect((void *)page, 0x2000, PROT_READ | PROT_WRITE | PROT_EXEC) != 0) {
        fprintf(stderr, "pp-cursor: mprotect W errno=%d func=%p\n", errno, func);
        return -1;
    }
    write_branch(func, hook);
    __builtin___clear_cache((char *)func, (char *)func + TRAMP_SIZE);
    mprotect((void *)page, 0x2000, PROT_READ | PROT_EXEC);
    g_swap_orig = (fn_swap_raw)make_orig_stub(func, orig);
    if (!g_swap_orig)
        return -1;
    fprintf(stderr, "pp-cursor: hooked %p -> %p stub=%p\n", func, hook, (void *)g_swap_orig);
    return 0;
}

static void *find_swap_func(void) {
    char exe[256];
    ssize_t elen = readlink("/proc/self/exe", exe, sizeof(exe) - 1);
    if (elen > 0)
        exe[elen] = 0;
    else
        strncpy(exe, XOCHITL_PATH, sizeof(exe) - 1);

    FILE *maps = fopen("/proc/self/maps", "r");
    if (!maps)
        return NULL;
    uintptr_t base = 0, map_off = 0;
    char line[512];
    while (fgets(line, sizeof(line), maps)) {
        if (!strstr(line, "r-xp"))
            continue;
        if (!strstr(line, exe) && !strstr(line, "/xochitl"))
            continue;
        char *p = line;
        base = strtoul(p, &p, 16);
        if (*p == '-')
            p++;
        strtoul(p, &p, 16);
        while (*p == ' ')
            p++;
        while (*p && *p != ' ')
            p++;
        while (*p == ' ')
            p++;
        map_off = strtoul(p, &p, 16);
        break;
    }
    fclose(maps);
    if (!base) {
        fprintf(stderr, "pp-cursor: no xochitl map\n");
        return NULL;
    }

    FILE *f = fopen(XOCHITL_PATH, "rb");
    if (!f)
        return NULL;
    fseek(f, 0, SEEK_END);
    long sz = ftell(f);
    fseek(f, 0, SEEK_SET);
    uint8_t *data = (uint8_t *)malloc((size_t)sz);
    if (!data) {
        fclose(f);
        return NULL;
    }
    if (fread(data, 1, (size_t)sz, f) != (size_t)sz) {
        free(data);
        fclose(f);
        return NULL;
    }
    fclose(f);

    const char *mark = MARKER;
    size_t mlen = strlen(mark);
    long fo = -1;
    for (long i = 0; i + (long)mlen < sz; i++) {
        if (memcmp(data + i, mark, mlen) == 0) {
            fo = i;
            break;
        }
    }
    if (fo < 0) {
        free(data);
        return NULL;
    }

    uint64_t e_shoff = *(uint64_t *)(data + 40);
    uint16_t e_shentsize = *(uint16_t *)(data + 58);
    uint16_t e_shnum = *(uint16_t *)(data + 60);
    uint16_t e_shstrndx = *(uint16_t *)(data + 62);
    uint64_t shstr_off = *(uint64_t *)(data + e_shoff + e_shstrndx * e_shentsize + 24);

    uint64_t str_vaddr = 0, text_addr = 0, text_off = 0, text_size = 0;
    for (int i = 0; i < e_shnum; i++) {
        uint8_t *sh = data + e_shoff + (size_t)i * e_shentsize;
        uint32_t name_off = *(uint32_t *)sh;
        uint64_t addr = *(uint64_t *)(sh + 16);
        uint64_t off = *(uint64_t *)(sh + 24);
        uint64_t size = *(uint64_t *)(sh + 32);
        const char *nm = (const char *)(data + shstr_off + name_off);
        if (off <= (uint64_t)fo && (uint64_t)fo < off + size)
            str_vaddr = addr + ((uint64_t)fo - off);
        if (strcmp(nm, ".text") == 0) {
            text_addr = addr;
            text_off = off;
            text_size = size;
        }
    }
    if (!str_vaddr || !text_size) {
        free(data);
        return NULL;
    }

    uint64_t xref_vaddr = 0;
    for (uint64_t i = 0; i + 8 < text_size; i += 4) {
        uint32_t w0 = *(uint32_t *)(data + text_off + i);
        if ((w0 & 0x9F000000u) != 0x90000000u)
            continue;
        uint32_t rd = w0 & 0x1F;
        uint32_t immlo = (w0 >> 29) & 3;
        uint32_t immhi = (w0 >> 5) & 0x1FFFFF;
        int64_t imm = (int64_t)((immhi << 2) | immlo);
        if (imm & (1 << 20))
            imm |= ~((1LL << 21) - 1);
        uint64_t pc = text_addr + i;
        uint64_t page = (pc & ~0xFFFull) + (uint64_t)(imm << 12);
        uint32_t w1 = *(uint32_t *)(data + text_off + i + 4);
        if ((w1 & 0xFFC00000u) == 0x91000000u) {
            uint32_t rn = (w1 >> 5) & 0x1F;
            uint32_t imm12 = (w1 >> 10) & 0xFFF;
            if ((w1 >> 22) & 1)
                imm12 <<= 12;
            if (rn == rd && page + imm12 == str_vaddr) {
                xref_vaddr = pc;
                break;
            }
        }
    }
    if (!xref_vaddr) {
        free(data);
        return NULL;
    }

    uint64_t xref_file = text_off + (xref_vaddr - text_addr);
    uint64_t func_file = 0;
    for (uint64_t back = 0; back < 0x2000; back += 4) {
        if (xref_file < back)
            break;
        uint64_t off = xref_file - back;
        if (*(uint32_t *)(data + off) == 0xD503233Fu) {
            func_file = off;
            break;
        }
    }
    free(data);
    if (!func_file)
        return NULL;

    uintptr_t runtime = (uintptr_t)base + (uintptr_t)func_file - (uintptr_t)map_off;
    fprintf(stderr, "pp-cursor: swap@ file=0x%llx runtime=%p\n",
            (unsigned long long)func_file, (void *)runtime);
    return (void *)runtime;
}

static int setup(void) {
    if (atomic_exchange(&g_install_once, 1) != 0)
        return 0;
    resolve_qt();
    void *spy = dlopen(SPY_PATH, RTLD_NOW | RTLD_GLOBAL);
    if (spy) {
        g_get_fb_real = (fn_get_fb)dlsym(spy, "getFramebufferConfig");
        fprintf(stderr, "pp-cursor: spy cfg=%p\n", (void *)g_get_fb_real);
    }
    void *fn = find_swap_func();
    if (!fn)
        return -1;
    if (*(uint32_t *)fn != 0xD503233Fu) {
        fprintf(stderr, "pp-cursor: bad prologue\n");
        return -1;
    }
    return install_hook(fn, (void *)hook_entry);
}

/* High-rate expose pump — does NOT call swapBuffers itself (thread-safe). */
static void *present_pump(void *arg) {
    (void)arg;
    uint32_t last_seq = 0;
    int idle_ticks = 0;
    while (g_run) {
        struct timespec ts = {0, POLL_NS};
        nanosleep(&ts, NULL);
        if (!atomic_load(&g_hooked))
            continue;

        CursorShm c;
        if (read_cursor(&c) != 0 || !(c.flags & 1u)) {
            idle_ticks = 0;
            continue;
        }

        int cx = 0, cy = 0;
        /* Always stamp latest position into FB RAM */
        composite_cursor(&cx, &cy);

        int moved = (c.seq != last_seq);
        last_seq = c.seq;
        if (moved)
            idle_ticks = 0;
        else
            idle_ticks++;

        /* While moving: expose every tick (~20 Hz).
         * While idle: still refresh ~4 Hz so the cross stays visible / not wiped. */
        if (moved || (idle_ticks % 5) == 0) {
            force_cursor_present(c.x, c.y);
            /* Every ~0.5s while active, full-screen expose so nothing is stuck */
            if ((idle_ticks % 10) == 0 && !moved) {
                int dw = atomic_load(&g_disp_w);
                int dh = atomic_load(&g_disp_h);
                force_expose_rect(0, 0, dw, dh);
            }
        }
        if (moved) {
            /* double-kick on motion for snappier first frame */
            force_cursor_present(c.x, c.y);
        }
    }
    return NULL;
}

static void *boot_thread(void *arg) {
    (void)arg;
    char exe[256];
    ssize_t elen = readlink("/proc/self/exe", exe, sizeof(exe) - 1);
    if (elen > 0)
        exe[elen] = 0;
    else
        exe[0] = 0;
    if (!strstr(exe, "xochitl")) {
        fprintf(stderr, "pp-cursor: skip hook (exe=%s)\n", exe);
        return NULL;
    }

    for (int i = 0; i < 100 && g_run; i++) {
        FramebufferConfig c = get_fb();
        if (c.framebufferAddress)
            break;
        struct timespec ts = {0, 100 * 1000 * 1000};
        nanosleep(&ts, NULL);
    }

    if (setup() == 0) {
        atomic_store(&g_hooked, 1);
        fprintf(stderr, "pp-cursor: ready (20Hz expose pump)\n");
        pthread_t th;
        if (pthread_create(&th, NULL, present_pump, NULL) == 0)
            pthread_detach(th);
    } else {
        fprintf(stderr, "pp-cursor: setup failed\n");
        atomic_store(&g_install_once, 0);
    }
    return NULL;
}

void _xovi_construct(void) {
    fprintf(stderr, "pp-cursor: construct (v3 high-rate expose)\n");
    g_run = 1;
    pthread_t th;
    if (pthread_create(&th, NULL, boot_thread, NULL) == 0)
        pthread_detach(th);
}
