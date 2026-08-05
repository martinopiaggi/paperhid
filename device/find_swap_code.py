#!/usr/bin/env python3
"""Find EPFramebuffer::swapBuffers(QRect...) code in xochitl by matching plugin bytes."""
import struct
import sys

PLUGIN = "/usr/lib/plugins/scenegraph/libqsgepaper.so"
XOCHITL = "/usr/bin/xochitl"
# from elf_syms: val=0x32b70 size=404 (file vaddr for FUNC)
# Need file offset = vaddr - section_addr + section_offset for .text

def elf64_sections(data):
    e_shoff = struct.unpack_from("<Q", data, 40)[0]
    e_shentsize = struct.unpack_from("<H", data, 58)[0]
    e_shnum = struct.unpack_from("<H", data, 60)[0]
    e_shstrndx = struct.unpack_from("<H", data, 62)[0]

    def shdr(i):
        off = e_shoff + i * e_shentsize
        return struct.unpack_from("<IIQQQQIIQQ", data, off)

    shstr = shdr(e_shstrndx)[4]
    out = {}
    for i in range(e_shnum):
        name_off, typ, flags, addr, off, size, link, info, al, es = shdr(i)
        name = data[shstr + name_off :].split(b"\0", 1)[0].decode()
        out[name] = dict(addr=addr, off=off, size=size, typ=typ)
    return out


def dyn_sym_addr(data, sym_name):
    secs = elf64_sections(data)
    dyn = secs[".dynsym"]
    dynstr = secs[".dynstr"]
    strtab = data[dynstr["off"] : dynstr["off"] + dynstr["size"]]
    entsize = 24
    for i in range(dyn["size"] // entsize):
        st = dyn["off"] + i * entsize
        st_name, st_info, st_other, st_shndx, st_value, st_size = struct.unpack_from(
            "<IBBHQQ", data, st
        )
        name = strtab[st_name:].split(b"\0", 1)[0].decode(errors="replace")
        if name == sym_name:
            return st_value, st_size
    return None, None


def vaddr_to_off(secs, vaddr):
    for name, s in secs.items():
        if s["addr"] <= vaddr < s["addr"] + s["size"] and s["size"] > 0:
            return s["off"] + (vaddr - s["addr"]), name
    return None, None


def main():
    needle = (
        "_ZN13EPFramebuffer11swapBuffersE5QRect12EPScreenMode6QFlagsINS_10UpdateFlagEE"
    )
    plug = open(PLUGIN, "rb").read()
    psec = elf64_sections(plug)
    vaddr, size = dyn_sym_addr(plug, needle)
    print(f"plugin sym vaddr=0x{vaddr:x} size={size}")
    off, secname = vaddr_to_off(psec, vaddr)
    print(f"plugin file off=0x{off:x} sec={secname}")
    blob = plug[off : off + size]
    # use first 64 bytes as signature (relocations may break later bytes)
    sig = blob[:64]
    print("sig", sig[:16].hex())

    xo = open(XOCHITL, "rb").read()
    idx = xo.find(sig)
    print(f"exact first64 in xochitl: {idx}")
    # try progressively shorter
    for n in (48, 32, 24, 16):
        i = xo.find(blob[:n])
        print(f"first{n}: {i}")
    # also search ignoring maybe one differing reloc - sliding match score
    best = (0, -1)
    step = 16
    window = 32
    # only search .text
    xsec = elf64_sections(xo)
    text = xsec.get(".text")
    if text:
        region = xo[text["off"] : text["off"] + text["size"]]
        base = text["off"]
        print(f"searching .text size={text['size']}")
        target = blob[:window]
        # hash-based: find matches of first 8 bytes then score
        key = target[:8]
        start = 0
        hits = 0
        while True:
            j = region.find(key, start)
            if j < 0:
                break
            chunk = region[j : j + window]
            if len(chunk) < window:
                break
            score = sum(1 for a, b in zip(chunk, target) if a == b)
            if score > best[0]:
                best = (score, base + j)
            hits += 1
            start = j + 1
            if hits > 500000:
                break
        print(f"best score {best[0]}/{window} at file_off=0x{best[1]:x}")
        if best[1] >= 0 and text:
            # runtime addr guess: need process map for xochitl executable
            print(f"file offset for trampoline candidate: 0x{best[1]:x}")


if __name__ == "__main__":
    main()
