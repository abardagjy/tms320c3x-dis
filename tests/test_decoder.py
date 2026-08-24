"""
Decoder tests.

Test words are constructed from the field layouts documented in the TMS320C3x
User's Guide (Figures 13-1 to 13-5, Figure 6-3) rather than copied from a
reference disassembler, so these check the implementation against the spec
rather than against another implementation's opinion.
"""

import pytest

from c3xdis import decode, isa
from c3xdis.boot import BootTable, interleave, to_words


# --------------------------------------------------------------------------- #
# helpers that build instruction words the way the User's Guide figures do
# --------------------------------------------------------------------------- #

def two_op(op, g, dst, src):
    """Figure 13-1: 000 | op:6 | G:2 | dst:5 | src:16"""
    return (0b000 << 29) | (op << 23) | (g << 21) | (dst << 16) | src


def three_op(op, t, dst, src1, src2):
    """Figure 13-2: 001 | op:6 | T:2 | dst:5 | src1:8 | src2:8"""
    return (0b001 << 29) | (op << 23) | (t << 21) | (dst << 16) | (src1 << 8) | src2


def indirect_operand(mod, arn, disp=0):
    """Figure 6-2: mod:5 | ARn:3 | disp:8"""
    return (mod << 11) | (arn << 8) | disp


# --------------------------------------------------------------------------- #
# two-operand, general addressing
# --------------------------------------------------------------------------- #

def test_register_mode():
    # ADDI R1, R2   -- opcode 0x04, G=00 register
    ins = decode(two_op(0x04, 0b00, 2, 1))
    assert ins.mnemonic == "ADDI"
    assert ins.operands == ["R1", "R2"]


def test_direct_mode_without_dp():
    ins = decode(two_op(0x10, 0b01, 0, 0x0869))
    assert ins.mnemonic == "LDI"
    assert ins.operands == ["@0869h", "R0"]
    assert ins.data_ref is None


def test_direct_mode_resolves_against_dp():
    # The User's Guide worked example: with DP=0x80, @98AEh addresses 0x8098AE.
    ins = decode(two_op(0x10, 0b01, 5, 0x98AE), dp=0x80)
    assert ins.data_ref == 0x8098AE


def test_immediate_mode():
    ins = decode(two_op(0x04, 0b11, 3, 0x1234))
    assert ins.operands == ["1234h", "R3"]


def test_indirect_mode_plain():
    ins = decode(two_op(0x10, 0b10, 0, indirect_operand(0b11000, 0)))
    assert ins.operands == ["*AR0", "R0"]


def test_indirect_mode_with_displacement():
    ins = decode(two_op(0x10, 0b10, 1, indirect_operand(0b00000, 3, 5)))
    assert ins.operands == ["*+AR3(5)", "R1"]


def test_indirect_postincrement_index_register():
    ins = decode(two_op(0x10, 0b10, 1, indirect_operand(0b01100, 2)))
    assert ins.operands == ["*AR2++(IR0)", "R1"]


def test_stores_print_register_first():
    # STI is a store: the register field is the SOURCE, the general operand the
    # DESTINATION, so they print in the opposite order to a load.
    ins = decode(two_op(0x2A, 0b10, 0, indirect_operand(0b11000, 0)))
    assert ins.mnemonic == "STI"
    assert ins.operands == ["R0", "*AR0"]


def test_single_register_operand_forms():
    assert decode(two_op(0x1C, 0, 5, 0)).operands == ["R5"]          # POP R5
    assert decode(two_op(0x1E, 0, 9, 0)).operands == ["AR1"]         # PUSH AR1
    assert decode(two_op(0x23, 0, 1, 0)).operands == ["R1"]          # ROL R1


def test_no_operand_forms():
    assert decode(two_op(0x0C, 0, 0, 0)).mnemonic == "IDLE"
    assert decode(two_op(0x19, 0, 0, 0)).mnemonic == "NOP"
    assert decode(two_op(0x19, 0, 0, 0)).operands == []


def test_nop_with_indirect_operand_keeps_it():
    ins = decode(two_op(0x19, 0b10, 0, indirect_operand(0b11000, 4)))
    assert ins.mnemonic == "NOP"
    assert ins.operands == ["*AR4"]


def test_ldp_alias():
    # LDI with an immediate into DP is spelled LDP by the assembler.
    ins = decode(two_op(0x10, 0b11, isa.DP, 0x0042))
    assert ins.mnemonic == "LDP"


def test_lopower_and_maxspeed_share_an_opcode():
    assert decode(two_op(0x21, 0, 0, 0)).mnemonic == "LOPOWER"
    assert decode(two_op(0x21, 0, 0, 1)).mnemonic == "MAXSPEED"


def test_all_documented_two_operand_opcodes_decode():
    for op in isa.TWO_OPERAND:
        assert decode(two_op(op, 0, 0, 0)).valid


def test_undefined_two_operand_opcode_is_invalid():
    ins = decode(two_op(0x3F, 0, 0, 0))
    assert not ins.valid
    assert ins.kind == isa.INVALID


# --------------------------------------------------------------------------- #
# three-operand
# --------------------------------------------------------------------------- #

def test_three_operand_all_registers():
    ins = decode(three_op(0x02, 0b00, 3, 1, 2))     # ADDI3 R1, R2, R3
    assert ins.mnemonic == "ADDI3"
    assert ins.operands == ["R1", "R2", "R3"]


def test_three_operand_mixed_indirect():
    # T=01 makes src1 indirect, src2 stays a register.
    src1 = (0b11000 << 3) | 2                       # *AR2
    ins = decode(three_op(0x02, 0b01, 0, src1, 4))
    assert ins.operands == ["*AR2", "R4", "R0"]


def test_three_operand_both_indirect():
    src1 = (0b11000 << 3) | 1
    src2 = (0b11000 << 3) | 6
    ins = decode(three_op(0x09, 0b11, 7, src1, src2))
    assert ins.mnemonic == "MPYF3"
    assert ins.operands == ["*AR1", "*AR6", "R7"]


def test_all_documented_three_operand_opcodes_decode():
    for op in isa.THREE_OPERAND:
        assert decode(three_op(op, 0, 0, 0, 0)).valid


# --------------------------------------------------------------------------- #
# program control
# --------------------------------------------------------------------------- #

def test_br_is_24_bit_absolute():
    # Figure 6-3(a). Verified empirically against real firmware: reading these
    # as PC-relative sends 99.97% of targets outside the image.
    ins = decode((0b0110000 << 25) | 0x042D05, addr=0x1000)
    assert ins.mnemonic == "BR"
    assert ins.target == 0x042D05
    assert ins.kind == isa.BRANCH


def test_brd_sets_delayed():
    ins = decode((0b0110000 << 25) | (1 << 24) | 0x001234)
    assert ins.mnemonic == "BRD"
    assert ins.delayed


def test_call_is_absolute_and_classified_as_a_call():
    ins = decode((0b0110001 << 25) | 0x042D80)
    assert ins.mnemonic == "CALL"
    assert ins.target == 0x042D80
    assert ins.kind == isa.CALL


def test_rptb_is_absolute():
    # TI's worked example: RPTB 127h leaves RE holding 0x127 literally.
    ins = decode((0b0110010 << 25) | 0x127)
    assert ins.mnemonic == "RPTB"
    assert ins.target == 0x127


def test_conditional_branch_is_pc_relative():
    # Figure 13-5(b), B=1. Displacement is relative to the incremented PC.
    word = (0b011010 << 26) | (1 << 25) | (0b00101 << 16) | 0x0004
    ins = decode(word, addr=0x2000)
    assert ins.mnemonic == "BEQ"
    assert ins.target == 0x2000 + 1 + 4
    assert ins.kind == isa.COND_BRANCH


def test_conditional_branch_negative_displacement():
    word = (0b011010 << 26) | (1 << 25) | (0b00110 << 16) | 0xFFFC   # -4
    ins = decode(word, addr=0x2000)
    assert ins.mnemonic == "BNE"
    assert ins.target == 0x2000 + 1 - 4


def test_delayed_conditional_branch_skips_the_delay_slots():
    word = (0b011010 << 26) | (1 << 25) | (1 << 21) | (0b00101 << 16) | 0
    ins = decode(word, addr=0x100)
    assert ins.mnemonic == "BEQD"
    assert ins.delayed
    assert ins.target == 0x100 + 1 + isa.DELAY_SLOTS


def test_unconditional_condition_code_renders_without_suffix():
    word = (0b011010 << 26) | (1 << 25) | (0b00000 << 16) | 0
    assert decode(word).mnemonic == "B"


def test_conditional_branch_register_form():
    word = (0b011010 << 26) | (0 << 25) | (0b00101 << 16) | 5
    ins = decode(word)
    assert ins.operands == ["R5"]
    assert ins.target is None


def test_dbcond_carries_the_auxiliary_register():
    word = (0b011011 << 26) | (1 << 25) | (3 << 22) | (0b00110 << 16) | 2
    ins = decode(word, addr=0x50)
    assert ins.mnemonic == "DBNE"
    assert ins.operands[0] == "AR3"


def test_returns():
    # Table A-1: RETIcond is 011110000, RETScond is 011110001.
    assert decode((0b011110000 << 23) | (0b00000 << 16)).mnemonic == "RETI"
    assert decode((0b011110001 << 23) | (0b00000 << 16)).mnemonic == "RETS"


def test_return_is_classified():
    assert decode((0b011110000 << 23)).kind == isa.RETURN


# --------------------------------------------------------------------------- #
# parallel instructions
# --------------------------------------------------------------------------- #

def test_parallel_multiply_add():
    ins = decode(0b10 << 30)
    assert ins.mnemonic == "MPYF3||ADDF3"
    assert ins.valid


def test_parallel_store_pairs():
    for op, (a, b) in isa.PARALLEL_STORE.items():
        ins = decode((0b11 << 30) | (op << 25))
        assert ins.mnemonic == f"{a}||{b}"
        assert ins.valid


def test_undefined_parallel_encoding_is_invalid():
    assert not decode((0b11 << 30) | (0x1F << 25)).valid


# --------------------------------------------------------------------------- #
# condition codes and registers
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("code,name", [
    (0b00000, ""), (0b00001, "LO"), (0b00101, "EQ"),
    (0b00110, "NE"), (0b01001, "GT"), (0b10100, "ZUF"),
])
def test_condition_names(code, name):
    assert isa.condition(code) == name


@pytest.mark.parametrize("n,name", [
    (0, "R0"), (7, "R7"), (8, "AR0"), (15, "AR7"),
    (16, "DP"), (20, "SP"), (27, "RC"),
])
def test_register_names(n, name):
    assert isa.register(n) == name


def test_every_indirect_mod_value_has_a_rendering():
    for mod in isa.INDIRECT:
        assert "?" not in isa.indirect(mod, 0, 1)


# --------------------------------------------------------------------------- #
# boot table
# --------------------------------------------------------------------------- #

def test_interleave_two_lanes():
    assert interleave([b"\x01\x03", b"\x02\x04"]) == b"\x01\x02\x03\x04"


def test_interleave_rejects_mismatched_lanes():
    with pytest.raises(ValueError):
        interleave([b"\x01", b"\x02\x03"])


def test_interleave_single_lane_is_identity():
    assert interleave([b"\x01\x02\x03\x04"]) == b"\x01\x02\x03\x04"


def _boot_image(blocks, width=16, strb=0x800):
    words = [width, strb]
    for dest, data in blocks:
        words += [len(data), dest] + list(data)
    words.append(0)
    return words


def test_boot_table_parses():
    t = BootTable(_boot_image([(0x809C00, [1, 2, 3]), (0x42D00, [4, 5])]))
    assert t.width == 16
    assert t.strb == 0x800
    assert len(t.blocks) == 2
    assert t.blocks[0].dest == 0x809C00
    assert t.blocks[0].size == 3
    assert t.entry == 0x809C00


def test_boot_table_memory_flattens_blocks():
    t = BootTable(_boot_image([(0x100, [0xAA, 0xBB])]))
    assert t.memory() == {0x100: 0xAA, 0x101: 0xBB}


def test_boot_table_rejects_bad_width():
    with pytest.raises(ValueError, match="memory width"):
        BootTable([7, 0x800, 0])


def test_boot_table_requires_terminator():
    with pytest.raises(ValueError, match="terminating"):
        BootTable([16, 0x800, 2, 0x100, 1, 2])


def test_boot_table_rejects_oversized_block():
    with pytest.raises(ValueError, match="runs past"):
        BootTable([16, 0x800, 999, 0x100, 1])


def test_to_words_is_little_endian_by_default():
    assert to_words(b"\x01\x00\x00\x00") == [1]
    assert to_words(b"\x00\x00\x00\x01", big_endian=True) == [1]
