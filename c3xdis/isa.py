"""
TMS320C3x instruction set tables.

Every table here is transcribed from the TMS320C3x/C33 User's Guide
(Texas Instruments, SPRU031F): Table A-1 (instruction opcodes), Table 6-1 (CPU
register machine addresses), Table 6-2 / 13-10 (indirect addressing mod field),
Table 13-12 (condition codes), and Figures 13-1 through 13-5 (addressing-mode
encodings).

The 'C3x is word-addressed: the smallest addressable unit is a 32-bit word, and
every instruction is exactly one word. Addresses in this module are therefore
word addresses, never byte addresses.
"""

# --------------------------------------------------------------------------- #
# CPU registers -- User's Guide Table 6-1
# --------------------------------------------------------------------------- #

REGISTERS = [
    "R0", "R1", "R2", "R3", "R4", "R5", "R6", "R7",
    "AR0", "AR1", "AR2", "AR3", "AR4", "AR5", "AR6", "AR7",
    "DP", "IR0", "IR1", "BK", "SP", "ST", "IE", "IF",
    "IOF", "RS", "RE", "RC",
]

DP = 16  # machine address of the data-page pointer, needed for the LDP alias

#: Highest defined CPU register machine address. 0..27 are the registers in
#: REGISTERS; 28..31 are encodable in a 5-bit field but do not exist on the
#: part. An instruction naming one is not a legal instruction, which makes it a
#: useful corruption detector -- see analysis.suspect_registers().
MAX_REGISTER = len(REGISTERS) - 1


def register(n):
    """Register name for a 5-bit machine address; unknown encodings stay numeric."""
    if 0 <= n < len(REGISTERS):
        return REGISTERS[n]
    return f"REG{n}"


# --------------------------------------------------------------------------- #
# Condition codes -- User's Guide Table 13-12
#
# Several codes have more than one accepted mnemonic (EQ and Z are both 00101,
# for instance). The disassembler emits the compare-flavoured spelling, which is
# what TI's own examples use.
# --------------------------------------------------------------------------- #

CONDITIONS = {
    0b00000: "U",     # unconditional
    0b00001: "LO",    # lower than                 (also C, carry)
    0b00010: "LS",    # lower than or same as
    0b00011: "HI",    # higher than
    0b00100: "HS",    # higher than or same as     (also NC)
    0b00101: "EQ",    # equal to                   (also Z)
    0b00110: "NE",    # not equal to               (also NZ)
    0b00111: "LT",    # less than                  (also N, negative)
    0b01000: "LE",    # less than or equal to
    0b01001: "GT",    # greater than               (also P, positive)
    0b01010: "GE",    # greater than or equal to   (also NN)
    0b01100: "NV",    # no overflow
    0b01101: "V",     # overflow
    0b01110: "NUF",   # no floating-point underflow
    0b01111: "UF",    # floating-point underflow
    0b10000: "NLV",   # no latched overflow
    0b10001: "LV",    # latched overflow
    0b10010: "NLUF",  # no latched floating-point underflow
    0b10011: "LUF",   # latched floating-point underflow
    0b10100: "ZUF",   # zero or floating-point underflow
}


def condition(code):
    """Condition suffix. 'U' (unconditional) renders as an empty suffix."""
    name = CONDITIONS.get(code)
    if name is None:
        return f"?{code:02X}"
    return "" if name == "U" else name


# --------------------------------------------------------------------------- #
# Indirect addressing -- User's Guide Table 6-2 / 13-10
#
# Operand layout is  mod:5 | ARn:3 | disp:8  (bits 15-11, 10-8, 7-0).
# {n} is the auxiliary register number, {d} the displacement.
# --------------------------------------------------------------------------- #

INDIRECT = {
    0b00000: "*+AR{n}({d})",
    0b00001: "*-AR{n}({d})",
    0b00010: "*++AR{n}({d})",
    0b00011: "*--AR{n}({d})",
    0b00100: "*AR{n}++({d})",
    0b00101: "*AR{n}--({d})",
    0b00110: "*AR{n}++({d})%",
    0b00111: "*AR{n}--({d})%",
    0b01000: "*+AR{n}(IR0)",
    0b01001: "*-AR{n}(IR0)",
    0b01010: "*++AR{n}(IR0)",
    0b01011: "*--AR{n}(IR0)",
    0b01100: "*AR{n}++(IR0)",
    0b01101: "*AR{n}--(IR0)",
    0b01110: "*AR{n}++(IR0)%",
    0b01111: "*AR{n}--(IR0)%",
    0b10000: "*+AR{n}(IR1)",
    0b10001: "*-AR{n}(IR1)",
    0b10010: "*++AR{n}(IR1)",
    0b10011: "*--AR{n}(IR1)",
    0b10100: "*AR{n}++(IR1)",
    0b10101: "*AR{n}--(IR1)",
    0b10110: "*AR{n}++(IR1)%",
    0b10111: "*AR{n}--(IR1)%",
    0b11000: "*AR{n}",
    0b11001: "*AR{n}++(IR0)B",
}

#: mod values whose syntax embeds an explicit displacement field.
INDIRECT_USES_DISP = frozenset(range(0b00000, 0b01000))


def indirect(mod, arn, disp):
    """Render an indirect operand. Undefined mod values are shown raw."""
    template = INDIRECT.get(mod)
    if template is None:
        return f"*?mod{mod:02b}?AR{arn}"
    return template.format(n=arn, d=disp)


# --------------------------------------------------------------------------- #
# Operand shapes for the two-operand group
#
# STD    "OP src, dst"        -- the common case
# STORE  "OP src, dst"        -- but the register field is the SOURCE and the
#                                general-addressing operand is the DESTINATION,
#                                so the two are printed in the opposite order
# SRC    "OP src"             -- general-addressing operand only
# REG    "OP reg"             -- register field only
# NONE   "OP"                 -- no operands
# --------------------------------------------------------------------------- #

STD, STORE, SRC, REG, NONE = "std", "store", "src", "reg", "none"

#: opcode (bits 28-23) -> (mnemonic, shape), for bits 31-29 == 000
TWO_OPERAND = {
    0x00: ("ABSF", STD),    0x01: ("ABSI", STD),    0x02: ("ADDC", STD),
    0x03: ("ADDF", STD),    0x04: ("ADDI", STD),    0x05: ("AND", STD),
    0x06: ("ANDN", STD),    0x07: ("ASH", STD),     0x08: ("CMPF", STD),
    0x09: ("CMPI", STD),    0x0A: ("FIX", STD),     0x0B: ("FLOAT", STD),
    0x0C: ("IDLE", NONE),   0x0D: ("LDE", STD),     0x0E: ("LDF", STD),
    0x0F: ("LDFI", STD),    0x10: ("LDI", STD),     0x11: ("LDII", STD),
    0x12: ("LDM", STD),     0x13: ("LSH", STD),     0x14: ("MPYF", STD),
    0x15: ("MPYI", STD),    0x16: ("NEGB", STD),    0x17: ("NEGF", STD),
    0x18: ("NEGI", STD),    0x19: ("NOP", SRC),     0x1A: ("NORM", STD),
    0x1B: ("NOT", STD),     0x1C: ("POP", REG),     0x1D: ("POPF", REG),
    0x1E: ("PUSH", REG),    0x1F: ("PUSHF", REG),   0x20: ("OR", STD),
    0x21: ("LOPOWER", NONE),
    0x22: ("RND", STD),     0x23: ("ROL", REG),     0x24: ("ROLC", REG),
    0x25: ("ROR", REG),     0x26: ("RORC", REG),    0x27: ("RPTS", SRC),
    0x28: ("STF", STORE),   0x29: ("STFI", STORE),  0x2A: ("STI", STORE),
    0x2B: ("STII", STORE),  0x2C: ("SIGI", NONE),   0x2D: ("SUBB", STD),
    0x2E: ("SUBC", STD),    0x2F: ("SUBF", STD),    0x30: ("SUBI", STD),
    0x31: ("SUBRB", STD),   0x32: ("SUBRF", STD),   0x33: ("SUBRI", STD),
    0x34: ("TSTB", STD),    0x35: ("XOR", STD),     0x36: ("IACK", SRC),
}

#: opcode (bits 28-23) -> mnemonic, for bits 31-29 == 001
THREE_OPERAND = {
    0x00: "ADDC3",  0x01: "ADDF3",  0x02: "ADDI3",  0x03: "AND3",
    0x04: "ANDN3",  0x05: "ASH3",   0x06: "CMPF3",  0x07: "CMPI3",
    0x08: "LSH3",   0x09: "MPYF3",  0x0A: "MPYI3",  0x0B: "OR3",
    0x0C: "SUBB3",  0x0D: "SUBF3",  0x0E: "SUBI3",  0x0F: "TSTB3",
    0x10: "XOR3",
}

#: bits 29-26 -> mnemonic pair, for bits 31-30 == 10 (multiply/ALU in parallel)
PARALLEL_MPY = {
    0b0000: ("MPYF3", "ADDF3"),
    0b0001: ("MPYF3", "SUBF3"),
    0b0010: ("MPYI3", "ADDI3"),
    0b0011: ("MPYI3", "SUBI3"),
}

#: bits 29-25 -> mnemonic pair, for bits 31-30 == 11 (op in parallel with store)
PARALLEL_STORE = {
    0x00: ("STF", "STF"),     0x01: ("STI", "STI"),
    0x02: ("LDF", "LDF"),     0x03: ("LDI", "LDI"),
    0x04: ("ABSF", "STF"),    0x05: ("ABSI", "STI"),
    0x06: ("ADDF3", "STF"),   0x07: ("ADDI3", "STI"),
    0x08: ("AND3", "STI"),    0x09: ("ASH3", "STI"),
    0x0A: ("FIX", "STI"),     0x0B: ("FLOAT", "STF"),
    0x0C: ("LDF", "STF"),     0x0D: ("LDI", "STI"),
    0x0E: ("LSH3", "STI"),    0x0F: ("MPYF3", "STF"),
    0x10: ("MPYI3", "STI"),   0x11: ("NEGF", "STF"),
    0x12: ("NEGI", "STI"),    0x13: ("NOT", "STI"),
    0x14: ("OR3", "STI"),     0x15: ("SUBF3", "STF"),
    0x16: ("SUBI3", "STI"),   0x17: ("XOR3", "STI"),
}

# --------------------------------------------------------------------------- #
# Instruction classes, used by callers doing control-flow analysis
# --------------------------------------------------------------------------- #

NORMAL = "normal"
BRANCH = "branch"           # unconditional transfer, does not return
COND_BRANCH = "cbranch"     # conditional transfer, falls through
CALL = "call"               # subroutine call, returns to the next word
RETURN = "return"
REPEAT = "repeat"           # RPTB / RPTS
TRAP = "trap"
INVALID = "invalid"

#: A delayed branch takes effect after the next three instructions execute.
DELAY_SLOTS = 3
