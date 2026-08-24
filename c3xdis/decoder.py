"""
TMS320C3x instruction decoder.

decode(word, addr) -> Instruction

Top-level dispatch is on the high bits of the 32-bit instruction word
(User's Guide Figures 13-1 through 13-5 and Figure 6-3):

    31-29 = 000     two-operand, general addressing
    31-29 = 001     three-operand
    31-28 = 0100    LDFcond
    31-28 = 0101    LDIcond
    31-25 = 0110000 BR / BRD          bit 24 = delayed
    31-25 = 0110001 CALL
    31-25 = 0110010 RPTB
    31-25 = 0110011 SWI
    31-26 = 011010  Bcond / BcondD
    31-26 = 011011  DBcond / DBcondD
    31-26 = 011100  CALLcond
    31-25 = 0111010 TRAPcond
    31-23 = 011110000 RETIcond
    31-23 = 011110001 RETScond
    31-30 = 10      parallel multiply/ALU
    31-30 = 11      parallel op with store
"""

from dataclasses import dataclass, field

from . import isa


@dataclass
class Instruction:
    """One decoded 'C3x instruction word."""

    addr: int
    word: int
    mnemonic: str = "???"
    operands: list = field(default_factory=list)
    kind: str = isa.NORMAL
    #: Branch/call destination as a word address, when statically known.
    target: int = None
    #: Data address referenced via direct addressing, if the DP value is known.
    data_ref: int = None
    #: True for delayed branches, whose three following words still execute.
    delayed: bool = False
    #: Set when the encoding is not a defined instruction.
    valid: bool = True

    def __str__(self):
        if not self.operands:
            return self.mnemonic
        # Operands are comma-separated, except that "||" separates the two
        # halves of a parallel instruction and a "; ..." token trails as a
        # comment; neither takes a comma.
        text = ""
        for operand in self.operands:
            if operand == "||":
                text += " || "
            elif operand.startswith(";"):
                text += f"   {operand}"
            elif not text or text.endswith(("|| ", " ")):
                text += operand
            else:
                text += f", {operand}"
        # Parallel mnemonics run past the operand column; keep a separator.
        pad = (self.mnemonic.ljust(10) if len(self.mnemonic) < 10
               else self.mnemonic + " ")
        return pad + text


def _sign16(v):
    return v - 0x10000 if v & 0x8000 else v


def _sign24(v):
    return v - 0x1000000 if v & 0x800000 else v


def _general_operand(word, g, dp=None):
    """
    Render the bits 15-0 source operand of a general-addressing instruction.

    Returns (text, data_ref). data_ref is the resolved word address for direct
    addressing when a data-page pointer value is supplied, else None.
    """
    low = word & 0xFFFF
    if g == 0b00:                                   # register
        return isa.register(low & 0x1F), None
    if g == 0b01:                                   # direct: DP:expr
        if dp is None:
            return f"@{low:04X}h", None
        return f"@{low:04X}h", ((dp & 0xFF) << 16) | low
    if g == 0b10:                                   # indirect
        mod = (low >> 11) & 0x1F
        arn = (low >> 8) & 0x07
        disp = low & 0xFF
        return isa.indirect(mod, arn, disp), None
    return f"{low:04X}h", None                      # immediate


def _three_operand_src(field_bits, indirect_mode):
    """
    Render one source of a three-operand instruction.

    field_bits is the 8-bit src1 (bits 15-8) or src2 (bits 7-0) field. In
    indirect mode it splits as mod:5 | ARn:3 with an implied displacement of 1;
    in register mode only the low 5 bits are used.
    """
    if indirect_mode:
        mod = (field_bits >> 3) & 0x1F
        arn = field_bits & 0x07
        return isa.indirect(mod, arn, 1)
    return isa.register(field_bits & 0x1F)


def _decode_two_operand(word, addr, dp):
    op = (word >> 23) & 0x3F
    g = (word >> 21) & 0x03
    reg = (word >> 16) & 0x1F

    entry = isa.TWO_OPERAND.get(op)
    if entry is None:
        return Instruction(addr, word, "???", kind=isa.INVALID, valid=False)
    mnemonic, shape = entry

    # LOPOWER and MAXSPEED share opcode 0x21 and differ in the low bits.
    if op == 0x21:
        mnemonic = "MAXSPEED" if (word & 0xFFFF) else "LOPOWER"
        return Instruction(addr, word, mnemonic)

    # LDP is LDI with an immediate operand into the data-page pointer.
    if op == 0x10 and reg == isa.DP and g == 0b11:
        return Instruction(addr, word, "LDP", [f"{word & 0xFFFF:04X}h"])

    if shape is isa.NONE:
        return Instruction(addr, word, mnemonic)

    if shape is isa.REG:
        return Instruction(addr, word, mnemonic, [isa.register(reg)])

    src, data_ref = _general_operand(word, g, dp)

    if shape is isa.SRC:
        # A bare NOP has no operand; NOP with an indirect operand modifies ARn.
        if op == 0x19 and g != 0b10:
            return Instruction(addr, word, "NOP")
        return Instruction(addr, word, mnemonic, [src], data_ref=data_ref)

    if shape is isa.STORE:
        # Register field is the source, general operand is the destination.
        return Instruction(addr, word, mnemonic,
                           [isa.register(reg), src], data_ref=data_ref)

    return Instruction(addr, word, mnemonic,
                       [src, isa.register(reg)], data_ref=data_ref)


def _decode_three_operand(word, addr):
    op = (word >> 23) & 0x3F
    t = (word >> 21) & 0x03
    dst = (word >> 16) & 0x1F

    mnemonic = isa.THREE_OPERAND.get(op)
    if mnemonic is None:
        return Instruction(addr, word, "???", kind=isa.INVALID, valid=False)

    src1 = _three_operand_src((word >> 8) & 0xFF, t & 0b01)
    src2 = _three_operand_src(word & 0xFF, t & 0b10)
    return Instruction(addr, word, mnemonic, [src1, src2, isa.register(dst)])


def _decode_load_cond(word, addr, dp, float_variant):
    cond = (word >> 23) & 0x1F
    g = (word >> 21) & 0x03
    dst = (word >> 16) & 0x1F
    src, data_ref = _general_operand(word, g, dp)
    base = "LDF" if float_variant else "LDI"

    # LDP ("load data page pointer") is the assembler's spelling for an
    # unconditional immediate load into DP. Compilers emit it constantly, and
    # it is far more readable than LDIU with a bare DP destination.
    if not float_variant and cond == 0 and dst == isa.DP and g == 0b11:
        return Instruction(addr, word, "LDP", [f"{word & 0xFFFF:04X}h"])

    # The unconditional member of the LDIcond/LDFcond family is spelled with a
    # U suffix, which keeps it distinct from the plain two-operand LDI/LDF.
    mnemonic = base + (isa.condition(cond) or "U")
    return Instruction(addr, word, mnemonic,
                       [src, isa.register(dst)], data_ref=data_ref)


def _decode_cond_branch(word, addr, mnemonic_base, kind, arn_field=False):
    """Bcond, DBcond and CALLcond share a layout (Figure 13-5)."""
    b = (word >> 25) & 1          # 0 = register operand, 1 = PC-relative
    delayed = bool((word >> 21) & 1)
    cond = (word >> 16) & 0x1F

    mnemonic = mnemonic_base + isa.condition(cond) + ("D" if delayed else "")

    if arn_field:                 # DBcond counts down an auxiliary register
        arn = (word >> 22) & 0x07
        prefix = [f"AR{arn}"]
    else:
        prefix = []

    if b:
        disp = _sign16(word & 0xFFFF)
        # The displacement is relative to the incremented PC; a delayed branch
        # resolves three words later still.
        target = addr + 1 + disp + (isa.DELAY_SLOTS if delayed else 0)
        return Instruction(addr, word, mnemonic, prefix + [f"{target:06X}h"],
                           kind=kind, target=target, delayed=delayed)

    reg = isa.register(word & 0x1F)
    return Instruction(addr, word, mnemonic, prefix + [reg],
                       kind=kind, delayed=delayed)


def decode(word, addr=0, dp=None):
    """
    Decode one 32-bit instruction word.

    addr is the word address the instruction sits at, used to resolve
    PC-relative branch targets. dp, when supplied, is the current data-page
    pointer value, used to resolve direct-addressing references.
    """
    word &= 0xFFFFFFFF

    top3 = word >> 29
    if top3 == 0b000:
        return _decode_two_operand(word, addr, dp)
    if top3 == 0b001:
        return _decode_three_operand(word, addr)

    top2 = word >> 30
    if top2 == 0b10:
        op = (word >> 26) & 0x0F
        pair = isa.PARALLEL_MPY.get(op)
        if pair is None:
            return Instruction(addr, word, "???", kind=isa.INVALID, valid=False)
        return _decode_parallel_mpy(word, addr, pair)
    if top2 == 0b11:
        op = (word >> 25) & 0x1F
        pair = isa.PARALLEL_STORE.get(op)
        if pair is None:
            return Instruction(addr, word, "???", kind=isa.INVALID, valid=False)
        return _decode_parallel_store(word, addr, pair)

    top4 = word >> 28
    if top4 == 0b0100:
        return _decode_load_cond(word, addr, dp, float_variant=True)
    if top4 == 0b0101:
        return _decode_load_cond(word, addr, dp, float_variant=False)

    return _decode_program_control(word, addr)


def _decode_program_control(word, addr):
    top7 = word >> 25
    top6 = word >> 26
    top9 = word >> 23

    if top7 == 0b0110000:                       # BR / BRD -- 24-bit absolute
        delayed = bool((word >> 24) & 1)
        target = word & 0xFFFFFF
        return Instruction(addr, word, "BRD" if delayed else "BR",
                           [f"{target:06X}h"], kind=isa.BRANCH,
                           target=target, delayed=delayed)

    if top7 == 0b0110001:                       # CALL -- 24-bit absolute
        target = word & 0xFFFFFF
        return Instruction(addr, word, "CALL", [f"{target:06X}h"],
                           kind=isa.CALL, target=target)

    if top7 == 0b0110010:                       # RPTB -- 24-bit absolute
        target = word & 0xFFFFFF
        return Instruction(addr, word, "RPTB", [f"{target:06X}h"],
                           kind=isa.REPEAT, target=target)

    if top7 == 0b0110011:                       # SWI
        return Instruction(addr, word, "SWI", kind=isa.TRAP)

    if top6 == 0b011010:
        return _decode_cond_branch(word, addr, "B", isa.COND_BRANCH)

    if top6 == 0b011011:
        return _decode_cond_branch(word, addr, "DB", isa.COND_BRANCH,
                                   arn_field=True)

    if top6 == 0b011100:
        return _decode_cond_branch(word, addr, "CALL", isa.CALL)

    if top7 == 0b0111010:                       # TRAPcond
        cond = (word >> 16) & 0x1F
        return Instruction(addr, word, "TRAP" + isa.condition(cond),
                           [f"{word & 0x1F}"], kind=isa.TRAP)

    if top9 == 0b011110000:                     # RETIcond
        cond = (word >> 16) & 0x1F
        return Instruction(addr, word, "RETI" + isa.condition(cond),
                           kind=isa.RETURN)

    if top9 == 0b011110001:                     # RETScond
        cond = (word >> 16) & 0x1F
        return Instruction(addr, word, "RETS" + isa.condition(cond),
                           kind=isa.RETURN)

    return Instruction(addr, word, "???", kind=isa.INVALID, valid=False)


def _parallel_fields(word):
    """Shared operand fields of both parallel groups -- Figure 13-3."""
    return {
        "d1": f"R{(word >> 23) & 1}",
        "d2": f"R{2 + ((word >> 22) & 1)}",
        "src1": isa.register((word >> 19) & 0x07),
        "src2": isa.register((word >> 16) & 0x07),
        "src3": isa.indirect((word >> 11) & 0x1F, (word >> 8) & 0x07, 1),
        "src4": isa.indirect((word >> 3) & 0x1F, word & 0x07, 1),
    }


def _decode_parallel_mpy(word, addr, pair):
    """
    Parallel multiply with add/subtract -- Figure 13-3.

        31-30 = 10 | op:4 | P:2 | d1 | d2 | src1:3 | src2:3 | src3:8 | src4:8

    The P field permutes which operands feed which half, and TI documents that
    mapping per instruction rather than generally. The common P=0 assignment is
    emitted here and the P value is always shown, so a reader can check it
    against the relevant instruction page when it matters.
    """
    mpy, alu = pair
    f = _parallel_fields(word)
    p = (word >> 24) & 0x03
    return Instruction(
        addr, word, f"{mpy}||{alu}",
        [f["src1"], f["src2"], f["d1"], "||",
         f["src3"], f["src4"], f["d2"], f"; P={p}"],
    )


#: First halves that take a single source, versus the 3-operand ALU forms.
_PARALLEL_UNARY = frozenset({
    "LDF", "LDI", "ABSF", "ABSI", "FIX", "FLOAT", "NEGF", "NEGI", "NOT",
})


def _decode_parallel_store(word, addr, pair):
    """
    An arithmetic or load operation in parallel with a store -- Figure 13-3.

        31-30 = 11 | op:5 | d1:1 | d2:1 | src1:3 | src2:3 | src3:8 | src4:8

    Operand assignment is verified against real compiler output: the block-copy
    loop that TI's C runtime emits uses LDI||STI to move *ARm into *ARn via a
    register, which pins the load to src4/dst1 and the store to src2/src3.
    """
    first, second = pair
    f = _parallel_fields(word)

    if first == second:
        # Dual loads take two destinations; dual stores take two register
        # sources. Both use src3 and src4 as the memory operands.
        if first.startswith("LD"):
            left = [f["src3"], f["d1"]]
            right = [f["src4"], f["d2"]]
        else:
            left = [f["src1"], f["src3"]]
            right = [f["src2"], f["src4"]]
    elif first in _PARALLEL_UNARY:
        left = [f["src4"], f["d1"]]
        right = [f["src2"], f["src3"]]
    else:
        left = [f["src1"], f["src4"], f["d1"]]
        right = [f["src2"], f["src3"]]

    return Instruction(addr, word, f"{first}||{second}",
                       left + ["||"] + right)
