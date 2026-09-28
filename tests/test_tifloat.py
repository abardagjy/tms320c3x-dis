"""
TI float formats: the encodings the User's Guide fixes, every limit, and the
round trip both ways.

Reference words are worked from the field layouts in SPRU031 section 4.3
(exponent, sign, fraction) rather than from another implementation, so as with
the decoder tests these check the code against the specification.
"""

import random

import pytest

from c3xdis import tifloat
from c3xdis.tifloat import decode16, decode32, decode40, encode32


def word(e, s, f):
    """A single-precision word from its fields: e signed, s in {0, 1}, f raw."""
    return ((e & 0xFF) << 24) | (s << 23) | f


# --------------------------------------------------------------------------- #
# single precision, 32 bits
# --------------------------------------------------------------------------- #

KNOWN_SINGLES = [
    (0x00000000, 1.0),                              # e=0, s=0, f=0: the all-zero word is ONE
    (0x01000000, 2.0),
    (0xFF000000, 0.5),                              # e=-1
    (0xFF800000, -1.0),                             # -2 * 2**-1: not 0x00800000
    (0x00800000, -2.0),                             # -2 * 2**0
    (0x80000000, 0.0),                              # e=-128 is zero
    (0x00400000, 1.5),                              # f = 2**22 is a half
    (0x00C00000, -1.5),                             # -2 + 0.5
    (0x7F7FFFFF, (2 - 2 ** -23) * 2.0 ** 127),      # most positive
    (0x81000000, 2.0 ** -127),                      # least positive
    (0x81FFFFFF, -(1 + 2 ** -23) * 2.0 ** -127),    # least negative
    (0x7F800000, -(2.0 ** 128)),                    # most negative
]


@pytest.mark.parametrize("w, value", KNOWN_SINGLES)
def test_known_singles_decode(w, value):
    assert decode32(w) == value


@pytest.mark.parametrize("w, value", KNOWN_SINGLES)
def test_known_singles_encode(w, value):
    assert encode32(value) == w


def test_limits_are_the_constants():
    assert decode32(0x7F7FFFFF) == tifloat.MAX_SINGLE
    assert decode32(0x81000000) == tifloat.MIN_SINGLE
    assert decode32(0x7F800000) == tifloat.MOST_NEGATIVE_SINGLE
    assert tifloat.ZERO_SINGLE == 0x80000000


def test_every_word_with_exponent_minus_128_is_zero():
    for f in (0, 1, 0x7FFFFF):
        for s in (0, 1):
            assert decode32(word(-128, s, f)) == 0.0


def test_speed_of_light_from_a_real_rom():
    # Found in an HP instrument ROM's .cinit at RAM 0x01087a: 2.99792458e8 to 24 bits.
    assert decode32(0x1C0EF3C2) == 299792448.0
    assert encode32(2.99792458e8) == 0x1C0EF3C2
    assert 2.99792458e8 - 299792448.0 == 10.0             # the rounding lost 10 m/s
    assert abs(299792448.0 - 2.99792458e8) <= 2 ** -24 * 2.99792458e8


def test_fields_are_where_the_manual_says():
    # e in 31..24, s in 23, f in 22..0, and nothing else touches them
    assert decode32(word(3, 0, 0)) == 8.0
    assert decode32(word(-3, 0, 0)) == 0.125
    assert decode32(word(0, 0, 1)) == 1 + 2 ** -23
    assert decode32(word(0, 1, 1)) == -2 + 2 ** -23
    assert tifloat.fields32(0x1C0EF3C2) == (28, 0, 0x0EF3C2)
    assert tifloat.fields32(0xFF800000) == (-1, 1, 0)


def test_decode32_is_not_ieee():
    import struct
    # The same bits under IEEE 754 are a different number of the same order
    # of magnitude, which is why struct.unpack is not an acceptable shortcut.
    assert struct.unpack("<f", struct.pack("<I", 0x1C0EF3C2))[0] != 299792448.0
    assert struct.unpack("<f", struct.pack("<I", 0x00000000))[0] == 0.0 != decode32(0)


def test_decode32_rejects_out_of_range():
    with pytest.raises(ValueError):
        decode32(1 << 32)
    with pytest.raises(ValueError):
        decode32(-1)


# --------------------------------------------------------------------------- #
# short immediate, 16 bits
# --------------------------------------------------------------------------- #

KNOWN_SHORTS = [
    (0x0000, 1.0),
    (0x1000, 2.0),
    (0xF000, 0.5),
    (0xF800, -1.0),
    (0x0800, -2.0),
    (0x8000, 0.0),                                  # e=-8 is zero
    (0x0400, 1.5),                                  # LDF 0400h, R0
    (0x0C00, -1.5),
    (0x77FF, (2 - 2 ** -11) * 2.0 ** 7),            # most positive, 255.9375
    (0x9000, 2.0 ** -7),                            # least positive
    (0x9FFF, -(1 + 2 ** -11) * 2.0 ** -7),          # least negative
    (0x7800, -(2.0 ** 8)),                          # most negative, -256
]


@pytest.mark.parametrize("h, value", KNOWN_SHORTS)
def test_known_shorts_decode(h, value):
    assert decode16(h) == value


def test_short_zero_ignores_the_other_bits():
    for h in (0x8000, 0x8001, 0x8FFF, 0x8800):
        assert decode16(h) == 0.0


def test_decode16_rejects_out_of_range():
    with pytest.raises(ValueError):
        decode16(0x10000)


# --------------------------------------------------------------------------- #
# extended precision, 40 bits
# --------------------------------------------------------------------------- #

KNOWN_EXTENDED = [
    ((0x00, 0x00000000), 1.0),
    ((0x01, 0x00000000), 2.0),
    ((0xFF, 0x80000000), -1.0),
    ((0x00, 0x80000000), -2.0),
    ((0x80, 0x12345678), 0.0),                                 # e=-128 is zero
    ((0x00, 0x40000000), 1.5),
    ((0x00, 0x00000001), 1 + 2 ** -31),                        # the low fraction bit
    ((0x7F, 0x7FFFFFFF), (2 - 2 ** -31) * 2.0 ** 127),         # most positive
    ((0x81, 0x00000000), 2.0 ** -127),                         # least positive
    ((0x81, 0xFFFFFFFF), -(1 + 2 ** -31) * 2.0 ** -127),       # least negative
    ((0x7F, 0x80000000), -(2.0 ** 128)),                       # most negative
]


@pytest.mark.parametrize("fields, value", KNOWN_EXTENDED)
def test_known_extended_decode(fields, value):
    assert decode40(*fields) == value


def test_extended_agrees_with_single_on_the_shared_bits():
    # A 32-bit load appends 8 zero fraction bits: the single at word w is the
    # extended value (w >> 24, (w & 0x00FFFFFF) << 8).
    for w in (0x1C0EF3C2, 0x00C00000, 0xFF800000, 0x7F7FFFFF, 0x81FFFFFF):
        assert decode40(w >> 24, (w & 0x00FFFFFF) << 8) == decode32(w)


def test_decode40_rejects_out_of_range():
    with pytest.raises(ValueError):
        decode40(0x100, 0)
    with pytest.raises(ValueError):
        decode40(0, 1 << 32)


# --------------------------------------------------------------------------- #
# encode32: the inverse, round to nearest
# --------------------------------------------------------------------------- #

def test_encode_decode_round_trips_every_word_shape():
    words = [0x1C0EF3C2, 0x00000001, 0x007FFFFF, 0x00FFFFFF, 0x7F000000,
             0x81000001, 0xFE123456, 0x0A800000, 0xE209705F]
    rng = random.Random(1996)
    words += [rng.getrandbits(32) for _ in range(2000)]
    for w in words:
        if (w >> 24) == 0x80:
            continue                              # every 0x80xxxxxx is zero; only the canonical one returns
        assert encode32(decode32(w)) == w, hex(w)


def test_zero_words_all_encode_to_the_canonical_zero():
    for w in (0x80000000, 0x80000001, 0x80FFFFFF):
        assert encode32(decode32(w)) == 0x80000000


def test_decode_encode_is_within_half_an_ulp():
    rng = random.Random(299792458)
    for _ in range(5000):
        x = rng.uniform(-1.0, 1.0) * 10.0 ** rng.randint(-36, 37)
        if abs(x) < 2 ** -126:                    # the underflow region flushes to zero by design
            continue
        y = decode32(encode32(x))
        assert abs(y - x) <= 2 ** -23 * abs(x), (x, y)
        assert abs(y - x) <= 2 ** -24 * abs(x) * (1 + 2 ** -22), (x, y)  # half an ulp, really


def test_encode_rounds_to_nearest_not_down():
    one_ulp = 2 ** -23
    assert encode32(1 + 0.4 * one_ulp) == 0x00000000
    assert encode32(1 + 0.6 * one_ulp) == 0x00000001
    assert encode32(2 - 0.4 * one_ulp) == 0x01000000     # carries into the exponent
    assert encode32(-1 - 0.4 * one_ulp) == 0xFF800000
    assert encode32(-1 - 0.6 * one_ulp) == 0x00FFFFFF     # s=1, f=2**23-1: -2 + (2**23 - 1)/2**23 at e=0
    assert encode32(-2 + 0.4 * one_ulp) == 0x00800000


def test_encode_underflow_flushes_and_overflow_raises():
    assert encode32(2.0 ** -128) == 0x80000000
    assert encode32(-(2.0 ** -128)) == 0x80000000
    assert encode32(1e-45) == 0x80000000
    assert encode32(tifloat.MAX_SINGLE) == 0x7F7FFFFF
    assert encode32(tifloat.MOST_NEGATIVE_SINGLE) == 0x7F800000
    with pytest.raises(OverflowError):
        encode32(2.0 ** 128)                              # one ulp past the most positive rounds up
    with pytest.raises(OverflowError):
        encode32(-(2.0 ** 128) * (1 + 2 ** -22))
    with pytest.raises(ValueError):
        encode32(float("nan"))
    with pytest.raises(ValueError):
        encode32(float("inf"))


def test_encode_accepts_ints():
    assert encode32(1) == 0x00000000
    assert encode32(-2) == 0x00800000
    assert encode32(0) == 0x80000000


# --------------------------------------------------------------------------- #
# the --all filter
# --------------------------------------------------------------------------- #

def test_plausible_keeps_constants_and_drops_the_disguised_integers():
    plausible = tifloat.plausible
    assert plausible(299792448.0, 0x1C0EF3C2)
    assert plausible(-1.5, 0x00C00000)
    assert plausible(1e-9, 0xE209705F)
    assert not plausible(1e-13)                                      # below the band
    assert not plausible(1e13)                                       # above it
    assert not plausible(0.0, 0x80000000)
    assert not plausible(2.0, 0x01000000)                            # an integer under 2**24
    assert plausible(2.0, 0x01000000, integers=True)
    assert plausible(2.0 ** 24, 0x18000000)                          # a large integer is fine
    assert not plausible(decode32(0x00000005), 0x00000005)           # a count: 1.0000006
    assert not plausible(decode32(0x0001238C), 0x0001238C)           # an address: 1.0089
    assert not plausible(decode32(0xFFFFFFFF), 0xFFFFFFFF)           # -1 as an integer: -0.50000006
    assert plausible(decode32(0x00000005), 0x00000005, integers=True)
    assert plausible(decode32(0x00100000), 0x00100000)               # 2**20 is the edge
    assert plausible(decode32(0x00000005))                           # no word: the rule cannot fire
