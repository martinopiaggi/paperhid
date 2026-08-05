#!/usr/bin/env python3
"""List dynamic/function symbols from an ELF (no binutils needed)."""
import struct
import sys


def read_syms(path, needle=None):
    data = open(path, "rb").read()
    if data[:4] != b"\x7fELF":
        print("not elf")
        return
    ei_class = data[4]  # 1=32 2=64
    ei_data = data[5]  # 1=le
    if ei_class != 2 or ei_data != 1:
        print("need ELF64 LE")
        return
    e_shoff = struct.unpack_from("<Q", data, 40)[0]
    e_shentsize = struct.unpack_from("<H", data, 58)[0]
    e_shnum = struct.unpack_from("<H", data, 60)[0]
    e_shstrndx = struct.unpack_from("<H", data, 62)[0]

    def shdr(i):
        off = e_shoff + i * e_shentsize
        return struct.unpack_from("<IIQQQQIIQQ", data, off)

    # sh_name, sh_type, sh_flags, sh_addr, sh_offset, sh_size, sh_link, sh_info, sh_addralign, sh_entsize
    shstr_off = shdr(e_shstrndx)[4]
    sections = []
    for i in range(e_shnum):
        name_off, typ, flags, addr, off, size, link, info, align, entsize = shdr(i)
        name = data[shstr_off + name_off :].split(b"\0", 1)[0].decode()
        sections.append((name, typ, off, size, link, entsize))

    def get_sec(n):
        for s in sections:
            if s[0] == n:
                return s
        return None

    for dynname in (".dynsym", ".symtab"):
        sec = get_sec(dynname)
        if not sec:
            continue
        _, typ, off, size, link, entsize = sec
        strsec = sections[link]
        stroff, strsize = strsec[2], strsec[3]
        strtab = data[stroff : stroff + strsize]
        if entsize == 0:
            entsize = 24
        print(f"== {dynname} count={size // entsize} ==")
        hits = 0
        for i in range(size // entsize):
            st = off + i * entsize
            st_name, st_info, st_other, st_shndx, st_value, st_size = struct.unpack_from(
                "<IBBHQQ", data, st
            )
            name = strtab[st_name:].split(b"\0", 1)[0].decode(errors="replace")
            bind = st_info >> 4
            t = st_info & 0xF
            if needle and needle not in name:
                continue
            if not needle and t != 2:  # STT_FUNC
                continue
            print(f"{name:80s} bind={bind} type={t} val=0x{st_value:x} size={st_size}")
            hits += 1
            if hits > 80:
                print("...")
                break
        if needle and hits == 0:
            print("(no hits)")


if __name__ == "__main__":
    path = sys.argv[1]
    needle = sys.argv[2] if len(sys.argv) > 2 else None
    read_syms(path, needle)
