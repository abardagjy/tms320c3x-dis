# c3xdis

A disassembler for the Texas Instruments **TMS320C3x** DSP family
(TMS320C30 / C31 / C32 / VC33), with support for the on-chip bootloader's
boot-table format.

Pure Python, no dependencies, MIT licensed.

## Why

There is no open-source option for this architecture. Ghidra ships processor
modules for 50-odd instruction sets but not the 'C3x; Capstone has the C64x but
not the C3x; IDA's module is commercial. Writing a SLEIGH specification is a
much larger job than writing the disassembler, so this is a standalone tool.

The 'C3x turns up in a lot of early-1990s instrumentation, audio gear and motion
control, so if you are looking at a ROM from that era and the code is 32-bit
words that look almost-but-not-quite like something you recognise, this may be
what you want.

## Install

```
git clone https://github.com/abardagjy/tms320c3x-dis
cd tms320c3x-dis
python3 -m c3xdis.cli --help
```

Python 3.8+. Nothing to install; `pip install -e .` also works if you want the
`c3xdis` entry point on your path.

## Use

The 'C3x is **word-addressed** — the smallest addressable unit is a 32-bit word
and every instruction is exactly one word. All addresses here are word
addresses, never byte addresses.

### Byte lanes

A 'C3x with a 16-bit external bus is fed by two 8-bit ROMs, and an 8-bit bus by
four. Pass the lane files **low lane first** and they are interleaved for you:

```
c3xdis info U2.bin U11.bin          # 16-bit bus, two lanes
c3xdis info rom.bin                 # already-flat image
```

If you get the lane order wrong you will know immediately: the boot table's
first word is a memory width, and it will not read as 8, 16 or 32.

### Commands

```
c3xdis info    LANE...     boot table summary plus decode-rate validation
c3xdis dis     LANE...     disassemble
c3xdis strings LANE...     find strings stored one character per 32-bit word
c3xdis cinit   LANE...     RAM as the C runtime's .cinit copy leaves it
c3xdis float   LANE...     TI single-precision floats: search .cinit, or decode words
c3xdis word    0x08200869  decode literal instruction words
```

Useful flags for `dis`:

```
--start / --end    address range
--dp N             assumed data-page pointer, to resolve @direct operands
--raw --base N     skip boot-table parsing, load flat at N
--big-endian       assemble words big-endian (default is little)
```

### Example

```
$ c3xdis dis U2.bin U11.bin --start 0x42D05 --end 0x42D22

_entry:
042D05:  50700004  LDP       0004h
042D06:  08342D00  LDI       @2D00h, SP
042D07:  080B0014  LDI       SP, AR3
...
042D19:  139B0001  RPTS      R1

loc_042D1A:    ; xrefs: 1
042D1A:  DA002120  LDI||STI  *AR0++(1), R0 || R0, *AR1++(1)
042D1B:  08010000  LDI       R0, R1
042D1C:  6A26FFFA  BNED      loc_042D1A
...
042D21:  620471F6  CALL      sub_0471F6
```

That is TI's C runtime startup: data-page and stack setup, the `.cinit`
block-copy loop built from `RPTS` plus a parallel `LDI||STI`, then the call into
`main`.

## Strings

TI's C compiler for the 'C3x makes `char` a full 32-bit word, because the part
is word-addressed and has no byte accesses. Strings are therefore stored one
character per word, which no ordinary `strings(1)` will find:

```
0x11bc8: 00000054 00000068 00000069 00000063 0000006b ...
              'T'      'h'      'i'      'c'      'k'
```

`c3xdis strings` finds them, and `c3xdis dis` annotates direct-address loads
that point at one.

## Boot tables

The 'C3x bootloader's table format is part of the chip, so any ROM meant to be
booted by a 'C31 or 'C32 looks like this:

```
word 0      external memory width in bits: 8, 16 or 32
word 1      value for the bus-control (STRB) register
repeating:
  word      block size in 32-bit words, 0 terminates
  word      destination address
  ...       that many data words
```

After loading, the bootloader branches to the destination of the **first**
block, which is how the entry point gets expressed — often as a one-word dummy
block that a later block overwrites.

`c3xdis info` parses this and loads each block at its destination address, so
disassembly comes out at the addresses the code actually runs at.

## RAM after `.cinit`

TI's C compiler puts every initialised variable's value in a `.cinit` segment that the startup code copies into RAM before `main`, so a disassembly alone cannot say what `LDI @2388h, R1` loads: the operand is an offset into the current data page and the value arrives at boot. `cinit` walks the segment and answers:

```
c3xdis cinit U2.bin U11.bin --at 0x2388 0x249A      # bare 16-bit values are @XXXXh on --dp (default 1)
c3xdis cinit U2.bin U11.bin --range 0x12303 0x12310
c3xdis cinit U2.bin U11.bin --find 0x10000000
```

The segment is found by validation: each boot-loaded block is walked as `[size][dest][data]` records and the one that consumes nearly all of itself is `.cinit`; a code block fails on its first word. `--start` overrides it. A cell that comes back uninitialised is `.bss`, written at runtime, and not in the ROM.

## Floating point

The 'C3x is a floating-point DSP and its formats are **not IEEE 754**. The exponent is an unbiased two's-complement integer in the top bits, the sign bit sits below it, and a negative mantissa is two's complement rather than sign-magnitude. There are no NaNs, infinities or denormals, and zero is not the all-clear word but the most negative exponent: every word with that exponent reads as zero whatever its other bits say. Feed a 'C3x word to `struct.unpack("f")` and you get a wrong number of about the right magnitude, which is the worst kind of wrong.

```
single, 32 bits     e:8 (31..24)   s (23)   f:23 (22..0)
short immediate     e:4 (15..12)   s (11)   f:11 (10..0)     the operand of LDF 0400h
extended, 40 bits   e:8 (39..32)   s (31)   f:31 (30..0)     what R0-R7 hold

value = (1 + f/2^23) * 2^e      s = 0
      = (-2 + f/2^23) * 2^e     s = 1
      = 0                       e = -128 (single, extended), e = -8 (short)

0x00000000   1.0                0x7F7FFFFF   most positive   (2 - 2^-23) * 2^127
0x80000000   0.0                0x81000000   least positive  2^-127
0xFF800000  -1.0                0x81FFFFFF   least negative  -(1 + 2^-23) * 2^-127
0x00800000  -2.0                0x7F800000   most negative   -2^128
```

So the all-zero word is one, and -1.0 is "-2 times 2^-1", not the word with just the sign bit set (that is -2.0). `c3xdis.tifloat` has `decode32`, `decode16`, `decode40` and `encode32` (round to nearest), and the `float` subcommand reads words or searches `.cinit` for a value:

```
$ c3xdis float 1C0EF3C2 00C00000
1C0EF3C2  299792448.0              e=28    s=0  f=0x0ef3c2
00C00000  -1.5                     e=0     s=1  f=0x400000

$ c3xdis float U2.bin U11.bin --find 2.99792458e8 1e-9
0x01087a  1c0ef3c2  299792448.0  ~ 299792458.0
0x014ba7  e209705f  9.999999717180685e-10  ~ 1e-09

$ c3xdis float U2.bin U11.bin --all | grep -i 'e-0[6-9]'
```

That first hit is a real one: the speed of light rounded to a 24-bit mantissa, found in a 1996 HP instrument ROM's `.cinit`, and the check that the formula was right before the manual was opened. `--find` takes a relative tolerance, `--tol`, default 2e-6, because the value you type has more digits than the ROM kept. Lane files go before `--find`, as the other subcommands are written.

`--all` prints every cell that decodes to a plausible constant: magnitude between 1e-12 and 1e12, not an exact integer below 2^24, and not a raw word that is itself a small integer or a 'C3x address (signed magnitude below 2^20), because those decode to 1.000x and 1.01x and would be most of the output. On that ROM the last rule takes the list from 8,315 lines to 161 out of 14,600 cells. It costs the constants in [1.0, 1.125) and (-0.5000006, -0.5]; `--integers` turns both integer rules off.

## Correctness

Every encoding table is transcribed from the TMS320C3x/C33 User's Guide
(Texas Instruments, SPRU031F): Table A-1 for opcodes, Table 6-1 for register
machine addresses, Table 6-2 for the indirect-addressing mod field, Table 13-12
for condition codes, and Figures 13-1 to 13-5 and 6-3 for the addressing-mode
layouts.

The test suite builds instruction words from those documented field layouts
rather than copying output from another disassembler, so it checks the
implementation against the specification rather than against someone else's
opinion.

```
python3 -m pytest tests/
```

### Validation against real firmware

Tests prove self-consistency; they do not prove the tables are right. The
stronger check is a large body of real compiled code. Against a 1996 HP
instrument ROM (377 kB, 94,455 words):

```
decode rate      99.4% overall
                100.0% across the 72,105-word main code block
branch targets   99.9% land inside the loaded image
```

For context, **60.6% of uniformly random 32-bit words decode as some defined
instruction** — the 'C3x encoding is dense. So a 100% rate on one word means
little, but 100% across 72,105 consecutive words does not happen by accident.

### Detecting corrupted words

`info` reports, per boot-table block, how many instructions name a CPU register
that does not exist. The 'C3x encodes a register in 5 bits, so 28-31 are
representable but undefined -- the part has 28. Real compiler output never names
one.

This catches what a decode rate cannot. Decode rate asks *is this a legal
opcode*, and a corrupted word very often still is. This asks whether the
operands make sense:

```
  region   decode rate   bad regs   range
  block2   100.0%             1   0x00042d00..0x000546a8  72105 words  <- entry
  block3    96.6%           780   0x00014ca5..0x000190a7  17411 words

WARNING: 1 instruction(s) in the entry block name a register that
         does not exist.
           0x051B7A  08FF0434  LDII      0434h, REG31   (dst = reg 31)
```

That is a real find: a 1996 instrument ROM with 11 drifted bits, in an image
whose decode rate was 100%. A second copy of the same chip from a sibling unit
read `LDI *+AR4(52), AR0` at that address, and the surrounding code -- three
identical five-instruction blocks -- made the correct value unambiguous.

The count is also a serviceable code-versus-data discriminator. A genuine code
block scores 0; the `.cinit` block above scores 780, because disassembling data
produces impossible registers constantly. **Only the entry block's count is
worth acting on**, which is why the warning is scoped to it.

### A documentation bug this turned up

TI's own manual contradicts itself on the 24-bit branches. The `BR` page gives
the operation as `src → PC` with "a 24-bit **unsigned** integer" operand, which
is absolute, while the prose on the same page calls it "a PC-relative branch".

The firmware settles it:

```
24-bit BR/CALL/RPTB read as ABSOLUTE      99.95% of targets land in the image
24-bit BR/CALL/RPTB read as PC-RELATIVE    0.03%                (n = 3104)
16-bit conditional branches, PC-relative  99.97%                (n = 6658)
```

So the 'C3x genuinely mixes the two: **24-bit `BR`/`BRD`/`CALL`/`RPTB` take
absolute addresses, while the 16-bit conditional forms (`Bcond`, `DBcond`,
`CALLcond`) are PC-relative** — measured from the incremented PC, and three
words further still for the delayed variants. This tool implements both, and
the `info` command reports the in-image target ratio so you can sanity-check
the interpretation against your own ROM.

## Limitations

- **Parallel instructions.** The `P` field of the multiply-with-ALU forms
  permutes which operands feed which half, and TI documents that mapping per
  instruction rather than generally. The common assignment is emitted and the
  `P` value is always shown so it can be checked. The operand assignment for
  the op-with-store forms is verified against real compiler output. Parallel
  instructions were 0.16% of the firmware used for validation.
- **No control-flow reconstruction.** Linear sweep only. That loses nothing on
  a fixed-width word-addressed ISA — the sweep cannot fall out of sync — but it
  does mean data embedded in a code region is disassembled as if it were code.
- **`IDLE2` decodes as `IDLE`.** They share an opcode and differ in bits the
  User's Guide documents only on the instruction page.
- **No assembler.** Disassembly only.

## Licence

MIT. See [LICENSE](LICENSE).

TMS320 is a trademark of Texas Instruments. This project is not affiliated with
or endorsed by TI.
