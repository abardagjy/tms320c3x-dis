"""Command-line interface for c3xdis."""

import argparse
import sys

from . import __version__, analysis, boot, isa
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
    if table is not None:
        print()
        print("  region   decode rate")
        for n, b in enumerate(table.blocks):
            addrs = range(b.dest, b.end)
            rate = analysis.decode_rate(instructions, addrs)
            print(f"  block{n}   {rate:6.1%}   0x{b.dest:08x}..0x{b.end - 1:08x}"
                  f"   {b.size} words")
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
