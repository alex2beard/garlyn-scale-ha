"""Pure body-composition algorithm boundary."""

from ..const import ALGORITHM_VERSION
from .bodyfat_2_2_2 import calculate_body_composition
from .models import (
    ActivityLevel,
    AlgorithmInput,
    AlgorithmProfile,
    BodyCompositionResult,
    ReferenceStandard,
    SegmentalImpedance,
    Sex,
    amr_factor,
)

__all__ = [
    "ALGORITHM_VERSION",
    "ActivityLevel",
    "AlgorithmInput",
    "AlgorithmProfile",
    "BodyCompositionResult",
    "ReferenceStandard",
    "SegmentalImpedance",
    "Sex",
    "amr_factor",
    "calculate_body_composition",
]
