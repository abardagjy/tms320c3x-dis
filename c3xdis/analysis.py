"""
Whole-image analysis: linear sweep, cross-references, labels, and a decode-rate
metric for validating that the decoder is actually right.

Nothing here needs the code to be well-behaved -- a linear sweep over a
word-addressed fixed-width ISA cannot lose sync, which is one of the nicer
properties of disassembling a 'C3x compared with a variable-length CISC.
"""

from collections import defaultdict

from . import isa
from .decoder import decode


class Image:
    """A sparse word-addressed memory image with optional region names."""

    def __init__(self):
        self.words = {}
        self.regions = []          # (start, end_exclusive, name)

    def add_region(self, start, words, name=""):
        for k, w in enumerate(words):
            self.words[start + k] = w
        self.regions.append((start, start + len(words), name))

    def __contains__(self, addr):
        return addr in self.words

    def __getitem__(self, addr):
        return self.words[addr]

    def get(self, addr, default=None):
        return self.words.get(addr, default)

    @property
    def addresses(self):
        return sorted(self.words)

    def region_of(self, addr):
        for start, end, name in self.regions:
            if start <= addr < end:
                return name
        return None


def sweep(image, dp=None):
    """Decode every word in the image. Returns {addr: Instruction}."""
    return {a: decode(image[a], a, dp) for a in image.addresses}


def decode_rate(instructions, addresses=None):
    """
    Fraction of words that decode to a defined instruction.

    This is the headline validation number. Compiled code in a genuine code
    region should sit well above 0.95; a low rate means either the decoder is
    wrong or the region is data, not code.
    """
    items = (instructions[a] for a in (addresses if addresses is not None
                                       else instructions))
    total = valid = 0
    for ins in items:
        total += 1
        valid += ins.valid
    return (valid / total) if total else 0.0


def suspect_registers(image, addresses=None):
    """
    Find instructions naming a CPU register that does not exist.

    The 'C3x encodes register operands in 5 bits, so 28..31 are representable
    but undefined -- the part has 28 registers. Real compiler output never names
    one. A hit is therefore strong evidence of a corrupted word rather than an
    unusual instruction.

    This catches what a decode-rate check cannot. Decode rate asks "is this a
    legal opcode", and a corrupted word very often still is; this asks whether
    the operands make sense. It found a genuine bit-rot defect in a 1996
    instrument ROM that a 100% decode rate had passed over.

    Yields (address, word, field_name, register_number).
    """
    for addr in (addresses if addresses is not None else image.addresses):
        w = image[addr]
        top3, top4 = w >> 29, w >> 28
        fields = []
        if top3 in (0b000, 0b001) or top4 in (0b0100, 0b0101):
            fields.append(("dst", (w >> 16) & 0x1F))
        if top3 == 0b000 and ((w >> 21) & 0x03) == 0b00:
            fields.append(("src", w & 0x1F))
        for name, reg in fields:
            if reg > isa.MAX_REGISTER:
                yield addr, w, name, reg
                break


def xrefs(instructions, image):
    """
    Build cross-references from statically resolvable branch and call targets.

    Returns (targets, calls, in_image_ratio) where targets maps a destination
    address to the list of addresses referencing it, calls is the subset that
    are subroutine calls, and in_image_ratio is the fraction of resolved targets
    that land inside the image.

    That ratio is the empirical check on branch semantics: if BR/CALL 24-bit
    displacements were being interpreted with the wrong base, targets would
    scatter outside the loaded regions and the ratio would collapse.
    """
    targets = defaultdict(list)
    calls = defaultdict(list)
    resolved = inside = 0

    for addr, ins in instructions.items():
        if ins.target is None or not ins.valid:
            continue
        resolved += 1
        if ins.target in image:
            inside += 1
        targets[ins.target].append(addr)
        if ins.kind == isa.CALL:
            calls[ins.target].append(addr)

    ratio = (inside / resolved) if resolved else 0.0
    return dict(targets), dict(calls), ratio


def make_labels(targets, calls, entry=None):
    """Name every referenced address: sub_ for call targets, loc_ otherwise."""
    labels = {}
    for addr in targets:
        labels[addr] = f"{'sub' if addr in calls else 'loc'}_{addr:06X}"
    if entry is not None:
        labels[entry] = "_entry"
    return labels


def find_strings(image, minlen=4):
    """
    Locate strings stored one character per 32-bit word.

    TI's C compiler for the 'C3x makes `char` a full 32-bit word, because the
    part is word-addressed and has no byte accesses. Strings therefore occupy
    one word per character, which no generic strings(1) will find.

    Yields (start_address, text).
    """
    run_start = None
    run = []
    prev = None
    for addr in image.addresses:
        w = image[addr]
        contiguous = prev is None or addr == prev + 1
        if 0x20 <= w < 0x7F and contiguous:
            if run_start is None:
                run_start = addr
            run.append(chr(w))
        else:
            if run_start is not None and len(run) >= minlen:
                yield run_start, "".join(run)
            run_start, run = None, []
            if 0x20 <= w < 0x7F:
                run_start, run = addr, [chr(w)]
        prev = addr
    if run_start is not None and len(run) >= minlen:
        yield run_start, "".join(run)


def annotate_data_refs(instructions, strings):
    """
    Map resolved direct-address references onto known strings.

    Returns {instruction_address: string} for references that land on or inside
    a known string, so a load of a message address shows the message.
    """
    starts = {}
    for addr, text in strings:
        for k in range(len(text)):
            starts[addr + k] = text[k:]

    out = {}
    for addr, ins in instructions.items():
        if ins.data_ref is not None and ins.data_ref in starts:
            out[addr] = starts[ins.data_ref]
    return out
