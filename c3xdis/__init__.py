"""
c3xdis -- a disassembler for the Texas Instruments TMS320C3x DSP family.

The 'C3x (TMS320C30/C31/C32/VC33) is a 32-bit floating-point DSP from the late
1980s. It is word-addressed: the smallest addressable unit is a 32-bit word, and
every instruction occupies exactly one. Addresses throughout this package are
word addresses.

All encoding tables are transcribed from the TMS320C3x/C33 User's Guide
(Texas Instruments, SPRU031F).
"""

__version__ = "0.1.0"

from .decoder import Instruction, decode          # noqa: F401
from .boot import BootTable, interleave, to_words  # noqa: F401

__all__ = ["Instruction", "decode", "BootTable", "interleave", "to_words",
           "__version__"]
