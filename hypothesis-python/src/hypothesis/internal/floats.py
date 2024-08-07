# This file is part of Hypothesis, which may be found at
# https://github.com/HypothesisWorks/hypothesis/
#
# Copyright the Hypothesis Authors.
# Individual contributors are listed in AUTHORS.rst and the git log.
#
# This Source Code Form is subject to the terms of the Mozilla Public License,
# v. 2.0. If a copy of the MPL was not distributed with this file, You can
# obtain one at https://mozilla.org/MPL/2.0/.

import math
import struct
from sys import float_info
from typing import (
    TYPE_CHECKING,
    Callable,
    Dict,
    Literal,
    Optional,
    SupportsFloat,
    Tuple,
    Union,
    overload,
)

if TYPE_CHECKING:
    from typing import TypeAlias
else:
    TypeAlias = object

SignedIntFormat: "TypeAlias" = Literal["!h", "!i", "!q"]
UnsignedIntFormat: "TypeAlias" = Literal["!H", "!I", "!Q"]
IntFormat: "TypeAlias" = Union[SignedIntFormat, UnsignedIntFormat]
FloatFormat: "TypeAlias" = Literal["!e", "!f", "!d"]

# Format codes for (int, float) sized types, used for byte-wise casts.
# See https://docs.python.org/3/library/struct.html#format-characters
STRUCT_FORMATS: Dict[int, Tuple[UnsignedIntFormat, FloatFormat]] = {
    16: ("!H", "!e"),
    32: ("!I", "!f"),
    64: ("!Q", "!d"),
}

TO_SIGNED_FORMAT: Dict[UnsignedIntFormat, SignedIntFormat] = {
    "!H": "!h",
    "!I": "!i",
    "!Q": "!q",
}


@overload
def reinterpret_bits(x: float, from_: str, to: FloatFormat) -> float: ...


@overload
def reinterpret_bits(x: float, from_: str, to: IntFormat) -> int: ...


def reinterpret_bits(x: float, from_: str, to: str) -> float:
    x = struct.unpack(to, struct.pack(from_, x))[0]
    assert isinstance(x, (float, int))
    return x


def float_of(x: SupportsFloat, width: int) -> float:
    assert width in (16, 32, 64)
    if width == 64:
        return float(x)
    elif width == 32:
        return reinterpret_bits(float(x), "!f", "!f")
    else:
        return reinterpret_bits(float(x), "!e", "!e")


def is_negative(x: SupportsFloat) -> bool:
    try:
        return math.copysign(1.0, x) < 0
    except TypeError:
        raise TypeError(
            f"Expected float but got {x!r} of type {type(x).__name__}"
        ) from None


def count_between_floats(x: float, y: float, width: int = 64) -> int:
    assert x <= y
    if is_negative(x):
        if is_negative(y):
            return float_to_int(x, width) - float_to_int(y, width) + 1
        else:
            return count_between_floats(x, -0.0, width) + count_between_floats(
                0.0, y, width
            )
    else:
        assert not is_negative(y)
        return float_to_int(y, width) - float_to_int(x, width) + 1


def float_to_int(value: float, width: int = 64) -> int:
    fmt_int, fmt_flt = STRUCT_FORMATS[width]
    return reinterpret_bits(value, fmt_flt, fmt_int)


def int_to_float(value: int, width: int = 64) -> float:
    fmt_int, fmt_flt = STRUCT_FORMATS[width]
    return reinterpret_bits(value, fmt_int, fmt_flt)


def next_up(value: float, width: int = 64) -> float:
    """Return the first float larger than finite `val` - IEEE 754's `nextUp`.

    From https://stackoverflow.com/a/10426033, with thanks to Mark Dickinson.
    """
    assert isinstance(value, float), f"{value!r} of type {type(value)}"
    if math.isnan(value) or (math.isinf(value) and value > 0):
        return value
    if value == 0.0 and is_negative(value):
        return 0.0
    fmt_int, fmt_flt = STRUCT_FORMATS[width]
    # Note: n is signed; float_to_int returns unsigned
    fmt_int_signed = TO_SIGNED_FORMAT[fmt_int]
    n = reinterpret_bits(value, fmt_flt, fmt_int_signed)
    if n >= 0:
        n += 1
    else:
        n -= 1
    return reinterpret_bits(n, fmt_int_signed, fmt_flt)


def next_down(value: float, width: int = 64) -> float:
    return -next_up(-value, width)


def next_down_normal(value: float, width: int, *, allow_subnormal: bool) -> float:
    value = next_down(value, width)
    if (not allow_subnormal) and 0 < abs(value) < width_smallest_normals[width]:
        return 0.0 if value > 0 else -width_smallest_normals[width]
    return value


def next_up_normal(value: float, width: int, *, allow_subnormal: bool) -> float:
    return -next_down_normal(-value, width, allow_subnormal=allow_subnormal)


# Smallest positive non-zero numbers that is fully representable by an
# IEEE-754 float, calculated with the width's associated minimum exponent.
# Values from https://en.wikipedia.org/wiki/IEEE_754#Basic_and_interchange_formats
width_smallest_normals: Dict[int, float] = {
    16: 2 ** -(2 ** (5 - 1) - 2),
    32: 2 ** -(2 ** (8 - 1) - 2),
    64: 2 ** -(2 ** (11 - 1) - 2),
}
assert width_smallest_normals[64] == float_info.min


def make_float_clamper(
    min_float: float = 0.0,
    max_float: float = math.inf,
    *,
    allow_zero: bool = False,  # Allows +0.0 (even if minfloat > 0)
) -> Optional[Callable[[float], float]]:
    """
    Return a function that clamps positive floats into the given bounds.

    Returns None when no values are allowed (min > max and zero is not allowed).
    """
    if max_float < min_float:
        if allow_zero:
            min_float = max_float = 0.0
        else:
            return None

    range_size = min(max_float - min_float, float_info.max)
    mantissa_mask = (1 << 52) - 1

    def float_clamper(float_val: float) -> float:
        if min_float <= float_val <= max_float:
            return float_val
        if float_val == 0.0 and allow_zero:
            return float_val
        # Outside bounds; pick a new value, sampled from the allowed range,
        # using the mantissa bits.
        mant = float_to_int(float_val) & mantissa_mask
        float_val = min_float + range_size * (mant / mantissa_mask)
        # Re-enforce the bounds (just in case of floating point arithmetic error)
        return max(min_float, min(max_float, float_val))

    return float_clamper


def sign_aware_lte(x: float, y: float) -> bool:
    """Less-than-or-equals, but strictly orders -0.0 and 0.0"""
    if x == 0.0 == y:
        return math.copysign(1.0, x) <= math.copysign(1.0, y)
    else:
        return x <= y


SMALLEST_SUBNORMAL = next_up(0.0)
SIGNALING_NAN = int_to_float(0x7FF8_0000_0000_0001)  # nonzero mantissa
assert math.isnan(SIGNALING_NAN)
assert math.copysign(1, SIGNALING_NAN) == 1
