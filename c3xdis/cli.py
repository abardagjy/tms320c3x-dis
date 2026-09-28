"""Command-line interface for c3xdis."""

import argparse
import sys

from . import __version__, analysis, boot, cinit, isa, tifloat
from .decoder import decode


def _auto_int(s):
    return int(s, 0)


def _build_image(args):
    """Load lane files into an Image, honouring --raw / --base."""
    words, table = boot.load(args.files, big_endian=args.big_endian)
    image = analysis.Image()

    if args.raw or table is None:
        if table is None and not args.raw:
            print("warning: no valid boot table found, loading flat at --base "
                  f"0x{args.base:x} (word 0 = 0x{words[0]:08x})", file=sys.stderr)
        image.add_region(args.base, words, "raw")
        return words, None, image

    for n, b in enumerate(table.blocks):
        image.add_region(b.dest, words[b.first_word:b.first_word + b.size],
                         f"block{n}")
    return words, table, image


def cmd_info(args):
    words, table, image = _build_image(args)
    print(f"image            {len(words)} words ({len(words) * 4} bytes)")
    if table is None:
        print("boot table       none (flat image)")
    else:
        print(table.describe())

    instructions = analysis.sweep(image)
    _, _, ratio = analysis.xrefs(instructions, image)
    print()
    print(f"decode rate      {analysis.decode_rate(instructions):.1%} "
          f"over {len(instructions)} words")
    print(f"branch targets   {ratio:.1%} land inside the image")

    if table is None:
        return 0

    # The bad-register check is only meaningful over code. Data disassembles to
    # nonsense that names impossible registers constantly, so reporting it
    # image-wide would drown the signal -- restrict it per block, which also
    # makes the column a decent code-versus-data discriminator.
    # The LAST block covering the entry address, not the first: a boot table
    # commonly starts with a one-word dummy block whose only job is to set the
    # branch target, which a later block then overwrites. Later blocks win, the
    # same rule BootTable.memory() uses.
    entry_block = None
    for n, b in enumerate(table.blocks):
        if b.dest <= table.entry < b.end:
            entry_block = n
    print()
    print("  region   decode rate   bad regs   range")
    per_block = {}
    for n, b in enumerate(table.blocks):
        addrs = range(b.dest, b.end)
        rate = analysis.decode_rate(instructions, addrs)
        bad = list(analysis.suspect_registers(image, addrs))
        per_block[n] = bad
        note = "  <- entry" if n == entry_block else ""
        print(f"  block{n}   {rate:6.1%}      {len(bad):8}   "
              f"0x{b.dest:08x}..0x{b.end - 1:08x}  {b.size} words{note}")

    bad = per_block.get(entry_block, [])
    if bad:
        print()
        print(f"WARNING: {len(bad)} instruction(s) in the entry block name a "
              "register that")
        print("         does not exist. Real compiler output never does this, so "
              "these are")
        print("         very likely corrupted words -- a failing ROM or a "
              "marginal read.")
        for addr, word, field, reg in bad[:10]:
            print(f"           0x{addr:06X}  {word:08X}  {decode(word, addr)}"
                  f"   ({field} = reg {reg})")
    return 0


def cmd_dis(args):
    words, table, image = _build_image(args)
    instructions = analysis.sweep(image, dp=args.dp)

    targets, calls, _ = analysis.xrefs(instructions, image)
    entry = table.entry if table else None
    labels = analysis.make_labels(targets, calls, entry) if args.labels else {}

    annotations = {}
    if args.strings:
        annotations = analysis.annotate_data_refs(
            instructions, analysis.find_strings(image))

    start = args.start if args.start is not None else min(image.words)
    end = args.end if args.end is not None else max(image.words) + 1

    prev = None
    for addr in image.addresses:
        if not (start <= addr < end):
            continue
        if prev is not None and addr != prev + 1:
            print(f"\n; ---- gap: 0x{prev + 1:06X}..0x{addr - 1:06X} ----\n")
        prev = addr

        if addr in labels:
            refs = targets.get(addr, [])
            print(f"\n{labels[addr]}:"
                  + (f"    ; xrefs: {len(refs)}" if refs else ""))

        ins = instructions[addr]
        text = str(ins)
        if ins.target is not None and ins.target in labels:
            text = text.replace(f"{ins.target:06X}h", labels[ins.target])

        comment = ""
        if addr in annotations:
            snippet = annotations[addr][:40].replace("\n", "\\n")
            comment = f'    ; "{snippet}"'
        elif not ins.valid:
            comment = "    ; not a defined instruction"

        print(f"{addr:06X}:  {ins.word:08X}  {text}{comment}")
    return 0


def cmd_strings(args):
    _, _, image = _build_image(args)
    for addr, text in analysis.find_strings(image, args.minlen):
        print(f"{addr:06X}  {text}")
    return 0


def cmd_cinit(args):
    words, table = boot.load(args.files, big_endian=args.big_endian)
    if table is None:
        print("no boot table: .cinit lives inside a boot-loaded block", file=sys.stderr)
        return 1
    try:
        if args.start is not None:
            ram, records = cinit.load_ram(table, args.start, args.end)
        else:
            found = cinit.find_cinit(table)
            if found is None:
                print("no block parses as .cinit; pass --start", file=sys.stderr)
                return 1
            ram, records, block = found
            print(f"# .cinit in block at 0x{block.dest:08x} ({block.size} words)", file=sys.stderr)
    except ValueError as e:
        print(e, file=sys.stderr)
        return 1
    print(f"# {records} .cinit records -> {len(ram)} initialised cells", file=sys.stderr)

    def show(addr):
        v = ram.get(addr)
        return "<uninit: .bss, written at runtime>" if v is None else f"0x{v:08x}  ({v})"

    if args.at:
        for a in args.at:
            # a bare 16-bit value is a @XXXXh direct operand on data page --dp
            full = a if a > 0xFFFF else (args.dp << 16) | a
            print(f"@{full & 0xFFFF:04x}h  0x{full:06x} = {show(full)}")
    if args.range:
        lo, hi = args.range
        for a in range(lo, hi + 1):
            v = ram.get(a)
            print(f"0x{a:06x} = " + ("----" if v is None else f"0x{v:08x}"))
    if args.find is not None:
        for a, v in sorted(ram.items()):
            if v == args.find:
                print(f"0x{a:06x} = 0x{v:08x}")
    if not (args.at or args.range or args.find is not None):
        for a, v in sorted(ram.items()):
            print(f"0x{a:06x} = 0x{v:08x}")
    return 0


def _load_cinit_ram(args):
    """The .cinit-initialised RAM of the lane files, the way cmd_cinit finds it:
    by validation, or from --start/--end. Returns (ram, records) or raises."""
    words, table = boot.load(args.files, big_endian=args.big_endian)
    if table is None:
        raise ValueError("no boot table: .cinit lives inside a boot-loaded block")
    if args.start is not None:
        return cinit.load_ram(table, args.start, args.end)
    found = cinit.find_cinit(table)
    if found is None:
        raise ValueError("no block parses as .cinit; pass --start")
    ram, records, block = found
    print(f"# .cinit in block at 0x{block.dest:08x} ({block.size} words)", file=sys.stderr)
    return ram, records


def cmd_float(args):
    """TI single-precision floats: decode literal words, or search .cinit."""
    if args.find is None and not args.all:
        # literal words: hex with or without 0x, the way one reads them off a dump
        for text in args.items:
            word = int(text, 16)
            e, s, f = tifloat.fields32(word)
            print(f"{word:08X}  {tifloat.decode32(word)!r:<24} e={e:<5} s={s}  f=0x{f:06x}")
        return 0

    args.files = args.items
    ram, records = _load_cinit_ram(args)
    print(f"# {records} .cinit records -> {len(ram)} initialised cells", file=sys.stderr)
    hits = 0
    for addr, word in sorted(ram.items()):
        value = tifloat.decode32(word)
        if args.find is not None:
            for want in args.find:
                # relative tolerance; zero can only be hit exactly
                if abs(value - want) <= args.tol * abs(want):
                    print(f"0x{addr:06x}  {word:08x}  {value!r}  ~ {want!r}")
                    hits += 1
                    break
        elif tifloat.plausible(value, word, args.integers):
            print(f"0x{addr:06x}  {word:08x}  {value!r}")
            hits += 1
    if args.find is not None and not hits:
        print("no cell decodes to a value that close", file=sys.stderr)
        return 1
    return 0


def cmd_word(args):
    """Decode instruction words given on the command line -- handy for testing."""
    for value in args.words:
        ins = decode(value, args.base)
        print(f"{args.base:06X}:  {value:08X}  {ins}")
    return 0


def main(argv=None):
    p = argparse.ArgumentParser(
        prog="c3xdis",
        description="Disassembler for the Texas Instruments TMS320C3x DSP family.",
    )
    p.add_argument("--version", action="version", version=f"c3xdis {__version__}")

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("files", nargs="+", metavar="LANE",
                        help="ROM image, or byte-lane files low first: two for a "
                             "16-bit bus, four for an 8-bit bus")
    common.add_argument("--big-endian", action="store_true",
                        help="assemble words big-endian (default little)")
    common.add_argument("--raw", action="store_true",
                        help="treat the image as flat, skipping boot-table parsing")
    common.add_argument("--base", type=_auto_int, default=0,
                        help="load address for a flat image (default 0)")

    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("info", parents=[common],
                       help="boot table summary and decode-rate validation")
    s.set_defaults(fn=cmd_info)

    s = sub.add_parser("dis", parents=[common], help="disassemble")
    s.add_argument("--start", type=_auto_int, help="first address")
    s.add_argument("--end", type=_auto_int, help="one past the last address")
    s.add_argument("--dp", type=_auto_int,
                   help="assumed data-page pointer, to resolve @direct operands")
    s.add_argument("--no-labels", dest="labels", action="store_false",
                   help="do not synthesise sub_/loc_ labels")
    s.add_argument("--no-strings", dest="strings", action="store_false",
                   help="do not annotate references to strings")
    s.set_defaults(fn=cmd_dis)

    s = sub.add_parser("strings", parents=[common],
                       help="find strings stored one character per 32-bit word")
    s.add_argument("--minlen", type=int, default=4)
    s.set_defaults(fn=cmd_strings)

    s = sub.add_parser("cinit", parents=[common],
                       help="RAM as the C runtime's .cinit copy leaves it")
    s.add_argument("--dp", type=_auto_int, default=1,
                   help="data page for bare @XXXXh operands given to --at (default 1)")
    s.add_argument("--start", type=_auto_int, help="the .cinit segment's address, if validation cannot find it")
    s.add_argument("--end", type=_auto_int, help="with --start: the last address to walk")
    s.add_argument("--at", type=_auto_int, nargs="+", metavar="ADDR",
                   help="cells to look up; a 16-bit value is a @XXXXh operand on --dp")
    s.add_argument("--range", type=_auto_int, nargs=2, metavar=("LO", "HI"), help="dump a range")
    s.add_argument("--find", type=_auto_int, metavar="VALUE", help="every cell holding a value")
    s.set_defaults(fn=cmd_cinit)

    s = sub.add_parser("float", help="TI single-precision floats: decode words, or search .cinit")
    s.add_argument("items", nargs="+", metavar="WORD|LANE",
                   help="hex words to decode; with --find or --all, the LANE files instead")
    s.add_argument("--find", type=float, nargs="+", metavar="VALUE",
                   help="every .cinit cell that decodes to within --tol of a VALUE")
    s.add_argument("--tol", type=float, default=2e-6,
                   help="relative tolerance for --find (default 2e-6, about 2**-19)")
    s.add_argument("--all", action="store_true",
                   help="every .cinit cell that decodes to a plausible float, for grepping")
    s.add_argument("--integers", action="store_true",
                   help="with --all: keep cells that look like integers, decoded or raw")
    s.add_argument("--big-endian", action="store_true",
                   help="assemble words big-endian (default little)")
    s.add_argument("--start", type=_auto_int, help="the .cinit segment's address, if validation cannot find it")
    s.add_argument("--end", type=_auto_int, help="with --start: the last address to walk")
    s.set_defaults(fn=cmd_float)

    s = sub.add_parser("word", help="decode literal instruction words")
    s.add_argument("words", nargs="+", type=_auto_int)
    s.add_argument("--base", type=_auto_int, default=0)
    s.set_defaults(fn=cmd_word)

    args = p.parse_args(argv)
    try:
        return args.fn(args)
    except (ValueError, OSError) as exc:
        print(f"c3xdis: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
