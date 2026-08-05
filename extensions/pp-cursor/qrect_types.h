#pragma once
#include <stdint.h>
/* Qt6 QRect storage (inclusive x1,y1,x2,y2) */
typedef struct QRect {
    int32_t x1, y1, x2, y2;
} QRect;
typedef const void *const_void_ptr;
