"""
RAM as the TI C3x C runtime leaves it: walking `.cinit`.

TI's C compiler for the 'C3x puts every initialised variable's value in a
`.cinit` segment that the startup code copies into RAM before `main`. So a
disassembly alone cannot say what `LDI @2388h, R1` loads: the operand is a
16-bit offset into the current data page, and the VALUE there is not in the
code at all -- it arrives at boot, out of `.cinit`. Every constant that
matters in a ROM of this kind (event masks, dispatch tables, hardware
register addresses) comes out this way.

The `.cinit` format is `[size][dest][size words of data]`, repeating, ended
by a zero size -- the same shape as the boot table itself. `find_cinit()`
identifies the segment by VALIDATION rather than by guessing at sizes: it
walks each loaded block as if it were `.cinit` and keeps the one that consumes
nearly all of itself. A code block fails on its first word (an instruction is
not a plausible record size), which is the point; picking the largest block
instead lands on the code and yields nothing.

An address that comes back uninitialised is not an error: it is `.bss`,
zeroed at startup and written at runtime, so its value is simply not in the
ROM.

    walk(mem, start, end)      -> (ram, records, consumed) for one block
    find_cinit(table)          -> (ram, records, block) for the block that parses
    load_ram(table, start)     -> (ram, records) with an explicit start, or found
"""

MAX_RECORD = 0x10000        # a .cinit record longer than this is not one
MAX_DEST = 0x900000         # the 'C3x address space of interest


def walk(mem, start, end):
    """Walk one block of `mem` ({address: word}) as .cinit records.

    Returns (ram, records, consumed): the cells initialised, how many records
    were read, and how many words of the block they took."""
    ram, records, p = {}, 0, start
    while p <= end:
        size = mem.get(p)
        if size is None or size == 0:
            break
        dest = mem.get(p + 1)
        if dest is None or size > MAX_RECORD or not (0 < dest < MAX_DEST):
            break
        for k in range(size):
            v = mem.get(p + 2 + k)
            if v is not None:
                ram[dest + k] = v
        records += 1
        p += 2 + size
    return ram, records, p - start


def find_cinit(table, min_block=100, min_score=0.5):
    """Find the boot-table block that parses as .cinit.

    Returns (ram, records, block) for the block whose .cinit walk consumes the
    largest fraction of it (above `min_score`), or None if no block does."""
    mem = table.memory()
    best = None
    for block in table.blocks:
        if block.size < min_block:
            continue
        ram, records, consumed = walk(mem, block.dest, block.end - 1)
        score = consumed / block.size
        if records and score > min_score and (best is None or score > best[0]):
            best = (score, ram, records, block)
    return None if best is None else best[1:]


def load_ram(table, start=None, end=None):
    """The .cinit-initialised RAM of a boot table: (ram, records).

    With `start`, walk from that address (to `end`, default 128 K words on);
    otherwise find the segment by validation. Raises ValueError if neither
    yields a record."""
    mem = table.memory()
    if start is not None:
        ram, records, _ = walk(mem, start, end if end is not None else start + 0x20000)
        if not records:
            raise ValueError(f"no .cinit records at 0x{start:06x}")
        return ram, records
    found = find_cinit(table)
    if found is None:
        raise ValueError("no block parses as .cinit; pass an explicit start")
    ram, records, _ = found
    return ram, records
