"""Compatible port of the verified MovingLife eight-electrode calculation.

The implementation mirrors the ARM64 floating-point operation order from
``bodyFatScaleAlg_2.2.2``. Native intermediates are IEEE-754 binary32 values;
rounding only the final result produces observably different output.
"""

from __future__ import annotations

import struct

from .models import AlgorithmInput, BodyCompositionResult

_HUNDRED = 100.0
_TEN_THOUSAND = 10_000.0

# The native body-fat regression selects the second coefficient set for heights
# below 140 cm. Literals below are exact decimal representations
# of the binary32 constants recovered from libBodyFatScaleAlg.so.
_ADULT_COEFFICIENTS = (
    91.15013122558594,
    23.5606689453125,
    3335.6005859375,
    -274.7619323730469,
    0.0932685136795044,
    -0.2915077209472656,
    -236.66610717773438,
    -1608.2816162109375,
    -72.61331176757812,
    -73.65965270996094,
    -210.2682647705078,
    -1938.8551025390625,
    -19.556093215942383,
    0.9841639399528503,
    275.0567321777344,
    141908.46875,
    920991.375,
    -861752.25,
    -110124.25,
    100032.8203125,
    6996.009765625,
    817.5169067382812,
    -8975.0107421875,
    -10833.333984375,
    -2.9952855110168457,
    -83.95362091064453,
    62.19171905517578,
    254.80224609375,
    -1.6132214069366455,
    -76.79109954833984,
)

_SHORT_STATURE_COEFFICIENTS = (
    27.6679630279541,
    -3.540698528289795,
    11753.126953125,
    -821.906982421875,
    0.24088577926158905,
    -0.08450105041265488,
    -9313.021484375,
    9817.7236328125,
    5.946317672729492,
    -30.250185012817383,
    -11026.4892578125,
    12095.9375,
    -67.23564147949219,
    -29.64738655090332,
    -418.5121154785156,
    19837.65234375,
    114439.9296875,
    -157095.328125,
    -69757.390625,
    58957.15625,
    -43612.328125,
    40694.89453125,
    28.77547264099121,
    2500.187744140625,
    13.609246253967285,
    -193.62193298339844,
    47.93819808959961,
    127.14289093017578,
    -0.0008690111571922898,
    -27.257966995239258,
)

_WATER_WEIGHT = 0.7294986248016357
_WATER_OFFSET = -0.3025745451450348
_WATER_BMI = 0.034528784453868866
_WATER_FAT = -0.7482841610908508

_MUSCLE_WATER = 1.274999976158142
_MUSCLE_OFFSET = -0.20000000298023224
_MUSCLE_FFM_LIMIT = 0.9300000071525574
_MUSCLE_LOW_FAT_LIMIT = 0.8999999761581421
_MUSCLE_HIGH_FAT_LIMIT = 0.2240000069141388

_BMR_FFM = 21.59000015258789
_BMR_OFFSET = 370.29998779296875


def _f32(value: float) -> float:
    """Round a value to the native IEEE-754 binary32 representation."""
    return struct.unpack("<f", struct.pack("<f", value))[0]


def _add(left: float, right: float) -> float:
    return _f32(_f32(left) + _f32(right))


def _sub(left: float, right: float) -> float:
    return _f32(_f32(left) - _f32(right))


def _mul(left: float, right: float) -> float:
    return _f32(_f32(left) * _f32(right))


def _div(left: float, right: float) -> float:
    return _f32(_f32(left) / _f32(right))


def _bmi(weight_kg: float, height_cm: float) -> float:
    scaled_weight = _mul(weight_kg, _TEN_THOUSAND)
    return _div(_div(scaled_weight, height_cm), height_cm)


def _body_fat_formula(data: AlgorithmInput, bmi: float) -> float:
    """Evaluate the 2.2.2 InBody regression in native operation order."""
    weight = _f32(data.weight_kg)
    height = _f32(data.profile.height_cm)

    z20 = data.bia_20khz
    z20_left_arm = _f32(z20.left_arm)
    z20_right_arm = _f32(z20.right_arm)
    z20_left_leg = _f32(z20.left_leg)
    z20_right_leg = _f32(z20.right_leg)
    z20_trunk = _f32(z20.trunk)

    z100 = data.bia_100khz
    z100_left_arm = _f32(z100.left_arm)
    z100_right_arm = _f32(z100.right_arm)
    z100_left_leg = _f32(z100.left_leg)
    z100_right_leg = _f32(z100.right_leg)
    z100_trunk = _f32(z100.trunk)

    coefficients = (
        _ADULT_COEFFICIENTS
        if height >= 140.0
        else _SHORT_STATURE_COEFFICIENTS
    )
    (
        c1,
        c2,
        c3,
        c4,
        c5,
        c6,
        c7,
        c8,
        c9,
        c10,
        c11,
        c12,
        c13,
        c14,
        c15,
        c16,
        c17,
        c18,
        c19,
        c20,
        c21,
        c22,
        c23,
        c24,
        c25,
        c26,
        c27,
        c28,
        c29,
        c30,
    ) = coefficients

    base = _add(_mul(bmi, c29), _mul(_div(weight, height), c28))
    term27 = _mul(_div(_div(weight, z20_trunk), bmi), c27)
    term26 = _mul(_div(_div(weight, z100_trunk), bmi), c26)

    trunk_sum = _add(z100_trunk, z20_trunk)
    term25 = _mul(_div(weight, trunk_sum), c25)
    term24 = _mul(
        _div(1.0, _add(z100_right_arm, z20_right_arm)), c24
    )
    term23 = _mul(_div(1.0, _add(z100_left_arm, z20_left_arm)), c23)
    term22 = _mul(
        _div(weight, _add(z100_left_leg, z20_left_leg)), c22
    )
    term21 = _mul(
        _div(weight, _add(z100_right_leg, z20_right_leg)), c21
    )

    z20_legs = _add(z20_right_leg, z20_left_leg)
    z100_legs = _add(z100_right_leg, z100_left_leg)
    z20_arms = _add(z20_right_arm, z20_left_arm)
    z100_arms = _add(z100_right_arm, z100_left_arm)

    term20 = _mul(_div(1.0, _mul(z20_legs, bmi)), c20)
    term19 = _mul(_div(1.0, _mul(z100_legs, bmi)), c19)
    term18 = _mul(_div(1.0, _mul(z20_arms, bmi)), c18)
    term17 = _mul(_div(1.0, _mul(z100_arms, bmi)), c17)

    all_arm_impedance = _add(_add(z100_right_arm, z20_arms), z100_left_arm)
    all_leg_impedance = _add(_add(z100_left_leg, z20_legs), z100_right_leg)
    term16 = _mul(_div(_div(weight, all_arm_impedance), height), c16)
    term15 = _mul(_div(weight, all_leg_impedance), c15)

    term14 = _mul(_div(weight, z20_right_arm), c14)
    term13 = _mul(_div(weight, z20_left_arm), c13)
    term12 = _mul(_div(weight, z20_right_leg), c12)
    term11 = _mul(_div(weight, z20_left_leg), c11)
    term10 = _mul(_div(weight, z100_right_arm), c10)
    term9 = _mul(_div(weight, z100_left_arm), c9)
    term8 = _mul(_div(weight, z100_right_leg), c8)
    term7 = _mul(_div(weight, z100_left_leg), c7)

    term6 = _mul(height, c6)
    term5 = _mul(weight, c5)
    term4 = _div(c4, trunk_sum)
    term3 = _div(_div(c3, trunk_sum), bmi)
    term2 = _div(_mul(z100_legs, c2), z20_legs)
    term1 = _div(_mul(z100_arms, c1), z20_arms)

    # The sequence is intentional: every FADD rounds to binary32.
    result = _add(base, term27)
    for term in (
        term26,
        term25,
        term24,
        term23,
        term22,
        term21,
        term20,
        term19,
        term18,
        term17,
        term16,
        term15,
        term14,
        term13,
        term12,
        term11,
        term10,
        term9,
        term8,
        term7,
        term6,
        term5,
        term4,
        term3,
        term2,
        term1,
    ):
        result = _add(result, term)
    return _add(c30, result)


def _body_fat_kg(data: AlgorithmInput, bmi: float) -> tuple[float, bool, bool]:
    weight = _f32(data.weight_kg)

    if bmi < 10.0:
        fat_kg = _f32(float(weight) * 0.04)
    elif bmi > 100.0:
        fat_kg = _mul(weight, 0.75)
    else:
        formula = _body_fat_formula(data, bmi)
        minimum = float(weight) * 0.04
        maximum = float(weight) * 0.75
        if minimum > float(formula):
            fat_kg = _f32(minimum)
        elif maximum < float(formula):
            fat_kg = _f32(maximum)
        else:
            fat_kg = formula

    # The caller in the native library rechecks the clamp with a 0.001 kg
    # tolerance and uses the flags to select safe water/muscle values.
    low_fat = float(_mul(weight, _f32(0.04))) + 0.001 > float(fat_kg)
    high_fat = float(weight) * 0.75 - 0.001 < float(fat_kg)
    return fat_kg, low_fat, high_fat


def _water_kg(
    weight: float,
    bmi: float,
    fat_kg: float,
    low_fat: bool,
    high_fat: bool,
) -> float:
    water = _mul(weight, _WATER_WEIGHT)
    water = _add(water, _WATER_OFFSET)
    water = _add(water, _mul(bmi, _WATER_BMI))
    water = _add(water, _mul(fat_kg, _WATER_FAT))

    if water <= 0.0:
        water = _mul(weight, 0.125)
    elif water >= weight:
        water = _mul(weight, 0.75)

    if low_fat:
        return _mul(weight, 0.75)
    if high_fat:
        return _mul(weight, 0.125)
    return water


def _muscle_kg(
    weight: float,
    fat_kg: float,
    water_kg: float,
    low_fat: bool,
    high_fat: bool,
) -> float:
    if low_fat:
        return _mul(weight, _MUSCLE_LOW_FAT_LIMIT)
    if high_fat:
        return _mul(weight, _MUSCLE_HIGH_FAT_LIMIT)

    fat_free_mass = _sub(weight, fat_kg)
    scaled_water = _mul(water_kg, _MUSCLE_WATER)
    candidate = _add(scaled_water, _MUSCLE_OFFSET)
    if candidate >= fat_free_mass:
        return _mul(fat_free_mass, _MUSCLE_FFM_LIMIT)
    if candidate > water_kg:
        return candidate
    return scaled_water


def calculate_body_composition(data: AlgorithmInput) -> BodyCompositionResult:
    """Calculate the eight outputs verified against MovingLife 5.12.9."""
    if not isinstance(data, AlgorithmInput):
        raise TypeError("data must be an AlgorithmInput")

    weight = _f32(data.weight_kg)
    height = _f32(data.profile.height_cm)
    bmi = _bmi(weight, height)
    fat_kg, low_fat, high_fat = _body_fat_kg(data, bmi)
    water_kg = _water_kg(weight, bmi, fat_kg, low_fat, high_fat)
    muscle_kg = _muscle_kg(
        weight,
        fat_kg,
        water_kg,
        low_fat,
        high_fat,
    )

    fat_free_mass = _sub(weight, fat_kg)
    bmr_value = _add(_mul(fat_free_mass, _BMR_FFM), _BMR_OFFSET)

    return BodyCompositionResult(
        body_fat_pct=_mul(_div(fat_kg, weight), _HUNDRED),
        body_fat_kg=fat_kg,
        muscle_pct=_mul(_div(muscle_kg, weight), _HUNDRED),
        muscle_kg=muscle_kg,
        body_water_pct=_mul(_div(water_kg, weight), _HUNDRED),
        body_water_kg=water_kg,
        bmr_kcal=int(float(bmr_value) + 0.5),
        bmi=bmi,
    )
