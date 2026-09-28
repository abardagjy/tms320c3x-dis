"""
TI's TMS320C3x floating-point formats, which are not IEEE 754.

The 'C3x predates any obligation to be IEEE, and its formats are built for a
fast barrel-shifter datapath rather than for interchange: the exponent is a
plain two's-complement integer (no bias), the sign bit sits BELOW the exponent
rather than above it, and a negative number's mantissa is two's-complement
rather than sign-magnitude. There are no NaNs, no infinities, no denormals.
Zero is not "all bits clear" but the most negative exponent, and every word
with that exponent is zero whatever its other bits say. Feed a 'C3x word to
`struct.unpack("f")` and you get nonsense of the right order of magnitude,
which is the worst kind.

Single precision, 32 bits (User's Guide, SPRU031, section 4.3.1 "Single-
Precision Floating-Point Format"):

    31..24    e   exponent, 8-bit two's complement, -128..127
    23        s   sign
    22..0     f   fraction, 23 bits

    value  =  (1 + f / 2**23) * 2**e       s == 0
           =  (-2 + f / 2**23) * 2**e      s == 1
           =  0                            e == -128, whatever s and f

So 1.0 is 0x00000000 and 0.0 is 0x80000000, the reverse of the intuition IEEE
builds. The negative mantissa runs from -2 (f = 0) up to -1 - 2**-23
(f = all ones), so the magnitudes covered by one exponent are 1.0 <= |x| < 2.0
for positives and 1.0 < |x| <= 2.0 for negatives: -1.0 is not 0x00800000
(that is -2.0) but 0xFF800000, "-2 times 2**-1".

    most positive     0x7F7FFFFF   (2 - 2**-23) * 2**127
    least positive    0x81000000   2**-127
    least negative    0x81FFFFFF   -(1 + 2**-23) * 2**-127
    most negative     0x7F800000   -2**128
    zero              0x80000000   canonical; 0x80xxxxxx all read as zero

Short immediate, 16 bits (section 4.3.2), the operand of `LDF` and the
three-operand float instructions with an immediate: the same construction on
narrower fields, 4-bit exponent, sign, 11-bit fraction, zero at e == -8.

Extended precision, 40 bits (section 4.3.3), the R0-R7 register format: the
same construction again with an 8-bit exponent, sign, and a 31-bit fraction.
This is what the registers hold; a 32-bit store truncates the low 8 fraction
bits and a 32-bit load appends 8 zeros, so it only shows up in a ROM as the
32-bit form. It is here because it costs six lines.

    decode32(word)                  -> float
    decode16(halfword)              -> float
    decode40(exp_byte, mantissa32)  -> float
    encode32(value)                 -> word, round to nearest

The formula was checked against a real ROM's `.cinit` before it was checked
against the manual: the word 0x1C0EF3C2 decodes to 299792448.0, which is
2.99792458e8 (the speed of light, in m/s) rounded to a 24-bit mantissa, and
that is the kind of coincidence a wrong formula does not produce.
"""

import math

MAX_SINGLE = (2 - 2 ** -23) * 2.0 ** 127        # 0x7F7FFFFF
MIN_SINGLE = 2.0 ** -127                         # 0x81000000
MOST_NEGATIVE_SINGLE = -(2.0 ** 128)             # 0x7F800000
ZERO_SINGLE = 0x80000000


def _decode(e, s, f, frac_bits):
    """The common construction: e two's complement already, s in {0, 1}, f raw."""
    if s:
        return (-2.0 + f / 2.0 ** frac_bits) * 2.0 ** e
    return (1.0 + f / 2.0 ** frac_bits) * 2.0 ** e


def _signed(field, bits):
    """A `bits`-wide two's-complement field as a Python int."""
    return field - (1 << bits) if field & (1 << (bits - 1)) else field


def decode32(word):
    """A 32-bit TI single-precision word as a float. Exact: 24 bits fit in 53."""
    if not 0 <= word <= 0xFFFFFFFF:
        raise ValueError(f"not a 32-bit word: 0x{word:x}")
    e = _signed(word >> 24, 8)
    if e == -128:
        return 0.0
    return _decode(e, (word >> 23) & 1, word & 0x7FFFFF, 23)


def decode16(halfword):
    """A 16-bit TI short-immediate as a float, the operand of `LDF 0400h`."""
    if not 0 <= halfword <= 0xFFFF:
        raise ValueError(f"not a 16-bit halfword: 0x{halfword:x}")
    e = _signed(halfword >> 12, 4)
    if e == -8:
        return 0.0
    return _decode(e, (halfword >> 11) & 1, halfword & 0x7FF, 11)


def decode40(exp_byte, mantissa32):
    """A 40-bit extended-precision register value, given as its exponent byte
    (bits 39..32) and its 32-bit mantissa word (sign in bit 31, fraction in
    30..0). Exact: 32 bits fit in 53."""
    if not 0 <= exp_byte <= 0xFF:
        raise ValueError(f"not an exponent byte: 0x{exp_byte:x}")
    if not 0 <= mantissa32 <= 0xFFFFFFFF:
        raise ValueError(f"not a 32-bit mantissa: 0x{mantissa32:x}")
    e = _signed(exp_byte, 8)
    if e == -128:
        return 0.0
    return _decode(e, mantissa32 >> 31, mantissa32 & 0x7FFFFFFF, 31)


def encode32(value):
    """The 32-bit TI single nearest to `value`, ties to even.

    Magnitudes below 2**-127 flush to zero, which is what the hardware does on
    underflow. Anything the format cannot hold at all -- above (2 - 2**-23) *
    2**127, below -2**128, NaN, infinity -- raises OverflowError or
    ValueError rather than wrapping, because a wrapped exponent is the one
    error this format makes silently."""
    value = float(value)
    if math.isnan(value) or math.isinf(value):
        raise ValueError(f"not representable: {value}")
    if value == 0.0:
        return ZERO_SINGLE
    m, k = math.frexp(abs(value))        # abs(value) = m * 2**k, 0.5 <= m < 1
    if value > 0:
        # 1.0 <= mantissa < 2.0: e = k - 1, mantissa = 2 m
        e, s = k - 1, 0
        f = round((2.0 * m - 1.0) * 2 ** 23)
        if f == 1 << 23:                 # rounded up to 2.0: that is 1.0 * 2**(e+1)
            e, f = e + 1, 0
    else:
        # 1.0 < |mantissa| <= 2.0, stored as -2 + f: an exact power of two is
        # f = 0 one exponent down, everything else is e = k - 1.
        s = 1
        if m == 0.5:
            e, f = k - 2, 0
        else:
            e = k - 1
            f = round((2.0 - 2.0 * m) * 2 ** 23)
            if f == 1 << 23:             # rounded to -1.0 * 2**e: that is -2 * 2**(e-1)
                e, f = e - 1, 0
    if e < -127:
        return ZERO_SINGLE
    if e > 127:
        raise OverflowError(f"{value!r} is outside the TI single range")
    return ((e & 0xFF) << 24) | (s << 23) | f


def fields32(word):
    """(e, s, f) of a 32-bit word, for display: e signed, f raw."""
    return _signed(word >> 24, 8), (word >> 23) & 1, word & 0x7FFFFF


def plausible(value, word=None, integers=False, lo=1e-12, hi=1e12):
    """Whether a decoded value looks like a constant someone typed.

    Every word decodes to SOMETHING under this format -- 0x00000005 is
    1.0000006 and the address 0x0001238c is 1.0089 -- so a dump of every cell
    as a float is mostly the ROM's integers wearing a disguise. Three rules:

      - the magnitude is inside [lo, hi];
      - unless `integers`, the value is not an exact integer below 2**24,
        which is what a small integer stored as a float looks like;
      - unless `integers`, and when `word` is given, the raw word read as a
        signed 32-bit integer is not below 2**20 in magnitude: that is a
        count or a 'C3x address, and it decodes into [1.0, 1.125) or
        (-0.5000006, -0.5], the only constants this rule can lose.

    On a 14,600-cell HP instrument ROM the first two rules pass 8,315 cells,
    5,385 of them the 1.000x band; the third brings it to 161."""
    mag = abs(value)
    if not lo <= mag <= hi:
        return False
    if integers:
        return True
    if mag < 2 ** 24 and value == int(value):
        return False
    if word is not None:
        signed = word - (1 << 32) if word & 0x80000000 else word
        if abs(signed) < 1 << 20:
            return False
    return True
