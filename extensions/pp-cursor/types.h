#pragma once
#include <stdint.h>
#include <stdbool.h>
typedef struct QRect {
    int32_t x1, y1, x2, y2;
} QRect;
typedef const void *const_void_ptr;
#define FBSPY_TYPE_RGB565 1
#define FBSPY_TYPE_RGBA 2
typedef struct FramebufferConfig {
    void *framebufferAddress;
    int width, height, type, bpl;
    bool requiresReload;
} FramebufferConfig;
