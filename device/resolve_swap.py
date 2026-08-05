#!/usr/bin/env python3
"""Locate swapBuffers-like function start and runtime address in live xochitl."""
import struct
import os

XOCHITL = "/usr/bin/xochitl"
# from find_string_xref
XREF_VADDR = 0xA78288
XREF_FILE = 0x678288


def find_xochitl_pid():
    for name in os.listdir("/proc"):
        if not name.isdigit():
            continue
        try:
            cmd = open(f"/proc/{name}/cmdline", "rb").read()
        except OSError:
            continue
        if cmd.startswith(b"/usr/bin/xochitl"):
            return int(name)
    return None


def main():
    data = open(XOCHITL, "rb").read()
    # walk back from xref for function start (max 0x800 bytes)
    # look for PACIBSP 0xD503233F or STP x29,x30,[sp,#-imm]! pattern
    start = XREF_FILE
    func_file = None
    for back in range(0, 0x1000, 4):
        off = XREF_FILE - back
        if off < 0:
            break
        w = struct.unpack_from("<I", data, off)[0]
        # pacibsp
        if w == 0xD503233F:
            func_file = off
            break
        # stp x29, x30, [sp, #-0x..]!
        if (w & 0xFFC003FF) == 0xA98003FD or (w & 0xFFC07FFF) == 0xA9BF7BFD:
            # might be prologue
            func_file = off
            # keep scanning for pacibsp before it
            continue
    print(f"xref_file=0x{XREF_FILE:x}")
    print(f"func_file_candidate=0x{func_file:x}" if func_file else "no prologue")
    if func_file:
        print("prologue bytes", data[func_file : func_file + 16].hex())

    pid = find_xochitl_pid()
    print("pid", pid)
    if not pid:
        return
    # parse maps for r-xp xochitl
    base = None
    with open(f"/proc/{pid}/maps") as f:
        for line in f:
            if "/usr/bin/xochitl" in line and "r-xp" in line:
                print("map", line.strip())
                start_s = line.split("-", 1)[0]
                base = int(start_s, 16)
                # file offset usually 0 for first exec segment
                parts = line.split()
                # offset field
                off = int(parts[2], 16)
                print(f"base=0x{base:x} map_off=0x{off:x}")
                if func_file is not None:
                    # runtime = base + (func_file - map_file_offset_of_text)
                    # first r-xp often maps file offset 0 including headers, or offset of text
                    runtime = base + (func_file - off)
                    print(f"runtime_func≈0x{runtime:x}")
                break

    # dump a few instructions before string load near xref
    if func_file:
        for i in range(0, 64, 4):
            w = struct.unpack_from("<I", data, func_file + i)[0]
            print(f"  +{i:02x}: {w:08x}")


if __name__ == "__main__":
    main()
