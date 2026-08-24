"""
TMS320C3x boot-table parsing and image loading.

The 'C3x on-chip bootloader reads a boot table from external memory (or the
serial port) and copies its blocks into RAM. The format is part of the chip, so
any ROM meant to be booted by a 'C31/'C32 looks like this:

    word 0      external memory width in bits: 8, 16 or 32
    word 1      value to load into the bus-control (STRB) register
    then repeating:
        word    block size in 32-bit words, 0 terminates the table
        word    destination address
        ...     that many data words

After loading, the bootloader branches to the destination address of the FIRST
block, which is how the entry point is expressed.

Byte lanes
----------
A 'C3x with a 16-bit external bus takes two 8-bit ROMs, and an 8-bit bus takes
four. `interleave()` reassembles the lanes into the 32-bit word stream, low lane
first. Getting the order wrong is obvious: the width header in word 0 will not
read as 8, 16 or 32.
"""

import struct


def interleave(lanes):
    """
    Reassemble byte lanes into a flat little-endian image.

    lanes[0] supplies the least significant byte of each group. Pass one lane
    for an already-flat image, two for a 16-bit bus, four for an 8-bit bus.
    """
    if not lanes:
        raise ValueError("no lanes given")
    n = len(lanes[0])
    if any(len(l) != n for l in lanes):
        raise ValueError("lane sizes differ: " + ", ".join(str(len(l)) for l in lanes))
    if len(lanes) == 1:
        return bytes(lanes[0])
    out = bytearray(n * len(lanes))
    for i, lane in enumerate(lanes):
        out[i::len(lanes)] = lane
    return bytes(out)


def to_words(image, big_endian=False):
    if len(image) % 4:
        image = image[: len(image) - (len(image) % 4)]
    fmt = f"{'>' if big_endian else '<'}{len(image) // 4}I"
    return list(struct.unpack(fmt, image))


class Block:
    """One boot-table block."""

    __slots__ = ("dest", "size", "first_word")

    def __init__(self, dest, size, first_word):
        self.dest = dest
        self.size = size
        self.first_word = first_word

    @property
    def end(self):
        """One past the last destination address."""
        return self.dest + self.size

    def __repr__(self):
        return (f"Block(dest=0x{self.dest:08x}, size={self.size}, "
                f"first_word=0x{self.first_word:x})")


class BootTable:
    """A parsed 'C3x boot table."""

    VALID_WIDTHS = (8, 16, 32)

    def __init__(self, words):
        if len(words) < 3:
            raise ValueError("image too short to hold a boot table")
        self.words = words
        self.width = words[0]
        self.strb = words[1]
        if self.width not in self.VALID_WIDTHS:
            raise ValueError(
                f"word 0 is 0x{self.width:08x}, not a memory width of 8, 16 or 32 "
                "-- the byte lanes are probably in the wrong order"
            )

        self.blocks = []
        self.terminator = None
        i = 2
        while i < len(words):
            size = words[i]
            if size == 0:
                self.terminator = i
                break
            if i + 2 + size > len(words):
                raise ValueError(
                    f"block at word 0x{i:x} claims {size} words, which runs past "
                    "the end of the image"
                )
            self.blocks.append(Block(words[i + 1], size, i + 2))
            i += 2 + size
        else:
            raise ValueError("boot table has no terminating zero-length block")

    @property
    def entry(self):
        """Entry point: the destination of the first block."""
        return self.blocks[0].dest if self.blocks else None

    @property
    def used_words(self):
        """Words consumed by the table, including headers and terminator."""
        return self.terminator + 1

    def memory(self):
        """Flatten the blocks into {word_address: word}, later blocks winning."""
        mem = {}
        for b in self.blocks:
            for k in range(b.size):
                mem[b.dest + k] = self.words[b.first_word + k]
        return mem

    def describe(self):
        lines = [
            f"memory width     {self.width} bits",
            f"STRB control     0x{self.strb:08x}",
            f"entry point      0x{self.entry:08x}" if self.entry is not None
            else "entry point      (none)",
            f"table uses       {self.used_words} words "
            f"({self.used_words * 4} bytes) of {len(self.words)}",
            "",
            "  #      size (words)   destination range              image word",
        ]
        for n, b in enumerate(self.blocks):
            lines.append(
                f"  {n:<3}  {b.size:>10}     0x{b.dest:08x}..0x{b.end - 1:08x}"
                f"     0x{b.first_word:05x}"
            )
        total = sum(b.size for b in self.blocks)
        lines.append(f"       {total:>10}  data words in {len(self.blocks)} blocks")
        return "\n".join(lines)


def load(paths, big_endian=False):
    """Read one or more lane files and return (words, BootTable or None)."""
    lanes = [open(p, "rb").read() for p in paths]
    words = to_words(interleave(lanes), big_endian)
    try:
        return words, BootTable(words)
    except ValueError:
        return words, None
