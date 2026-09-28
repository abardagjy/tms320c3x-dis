"""The .cinit walker: found by validation, values land at their addresses, .bss stays uninitialised."""
from c3xdis import boot, cinit


def make_table(blocks):
    """A boot table image from [(dest, [words]), ...]: width 16, STRB 0, terminator."""
    words = [16, 0]
    for dest, data in blocks:
        words += [len(data), dest] + list(data)
    words.append(0)
    return boot.BootTable(words)


def test_cinit_is_found_by_validation_and_values_land():
    code = [0x08600001, 0x6A050003, 0x0F400000, 0x0F400000] * 40      # 160 words that are not records
    seg = [2, 0x012388, 0x10000000, 0x04000000,                        # two words at 0x012388
           1, 0x014636, 0xDEADBEEF]                                     # one word at 0x014636
    for i in range(40):                                                 # a realistic segment fills its block
        seg += [1, 0x013000 + i, i]
    seg.append(0)                                                       # terminator
    table = make_table([(0x042D00, code), (0x043000, seg)])
    ram, records, block = cinit.find_cinit(table)
    assert block.dest == 0x043000
    assert records == 42
    assert ram[0x012388] == 0x10000000 and ram[0x012389] == 0x04000000
    assert ram[0x014636] == 0xDEADBEEF
    assert 0x012400 not in ram                                          # .bss: not in the ROM
    ram2, records2 = cinit.load_ram(table)
    assert ram2 == ram and records2 == 42


def test_explicit_start_and_no_cinit():
    code = [0x08600001] * 120
    table = make_table([(0x042D00, code)])
    assert cinit.find_cinit(table) is None
    seg = [1, 0x012000, 7, 0] + [0] * 100
    table = make_table([(0x042D00, code), (0x050000, seg)])
    ram, records = cinit.load_ram(table, 0x050000)
    assert records == 1 and ram[0x012000] == 7
    try:
        cinit.load_ram(table, 0x042D00, 0x042D10)
        assert False, "code should not parse as .cinit"
    except ValueError:
        pass
