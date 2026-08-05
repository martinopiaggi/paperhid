#!/usr/bin/env python3
"""Find ARM64 code references to a string in xochitl (stripped)."""
import struct
import sys

def sections(data):
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
        name = data[shstr + name_off:].split(b"\0", 1)[0].decode()
        out[name] = (addr, off, size)
    return out

def main():
    path = sys.argv[1]
    needle = sys.argv[2].encode()
    data = open(path, "rb").read()
    secs = sections(data)
    # find string file offset and vaddr
    fo = data.find(needle)
    if fo < 0:
        print("string not found")
        return
    # which section?
    str_vaddr = None
    for name, (addr, off, size) in secs.items():
        if off <= fo < off + size:
            str_vaddr = addr + (fo - off)
            print(f"string in {name} file=0x{fo:x} vaddr=0x{str_vaddr:x}")
            break
    if str_vaddr is None:
        print(f"string file=0x{fo:x} (no section)")
        return
    text = secs.get(".text")
    if not text:
        print("no .text")
        return
    taddr, toff, tsize = text
    code = data[toff : toff + tsize]
    # scan ADRP + ADD pairs that could form str_vaddr
    # ADRP Xd, imm: 1xx10000 immlo[30:29] immhi[23:5] Rd[4:0]
    hits = []
    for i in range(0, len(code) - 8, 4):
        w0 = struct.unpack_from("<I", code, i)[0]
        if (w0 & 0x9F000000) != 0x90000000:  # ADRP
            continue
        rd = w0 & 0x1F
        immlo = (w0 >> 29) & 3
        immhi = (w0 >> 5) & 0x1FFFFF
        imm = (immhi << 2) | immlo
        if imm & (1 << 20):
            imm |= ~((1 << 21) - 1)  # sign extend 21
        pc = taddr + i
        page = (pc & ~0xFFF) + (imm << 12)
        w1 = struct.unpack_from("<I", code, i + 4)[0]
        # ADD Xd, Xn, #imm12  (0x91000000) or ADR
        if (w1 & 0xFFC00000) == 0x91000000:
            rn = (w1 >> 5) & 0x1F
            rd2 = w1 & 0x1F
            imm12 = (w1 >> 10) & 0xFFF
            sh = (w1 >> 22) & 1
            if sh:
                imm12 <<= 12
            if rn == rd:
                target = page + imm12
                if target == str_vaddr:
                    hits.append(pc)
        # also ADRP + LDR
        if (w1 & 0xFFC00000) == 0xF9400000:  # LDR Xt,[Xn,#imm]
            rn = (w1 >> 5) & 0x1F
            imm12 = ((w1 >> 10) & 0xFFF) << 3
            if rn == rd and page + imm12 == str_vaddr:
                hits.append(pc)
    print(f"xref hits: {len(hits)}")
    for h in hits[:20]:
        print(f"  code vaddr 0x{h:x} file 0x{toff + (h - taddr):x}")

if __name__ == "__main__":
    main()
