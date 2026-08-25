# `bodyFatScaleAlg_2.2.2` compatibility notes

The pure Python calculation in
`custom_components/garlyn_scale/algorithm/bodyfat_2_2_2.py` is a static port of
the ARM64 implementation shipped with MovingLife 5.12.9. It does not load or
execute code from the APK at runtime.

## Binary provenance

- library: `lib/arm64-v8a/libBodyFatScaleAlg.so`;
- SHA-256:
  `9e14c0b471a04d9c64a719a0b998f64266fa3f060395e682d012cb976a81d2d6`;
- ELF build ID: `21d77e5cd206611eef53940bb54befcf5392158a`;
- algorithm path: type `2`, InBody dual-frequency eight-electrode calculation;
- requested version: `bodyFatScaleAlg_2.2.2`.

The native `versionBefore` name is misleading: it returns true only when the
requested version is newer than the comparison version. Therefore 2.2.2 takes
the coefficient branch used by this port.

## Impedance layout

The public integration model uses the documented transport order:

1. left arm;
2. right arm;
3. left leg;
4. right leg;
5. trunk.

The JNI structure uses a different internal layout. At each frequency it stores
trunk, right arm, left arm, right leg, and left leg. The port uses named fields
and performs no ambiguous positional indexing.

## Numeric compatibility

The ARM64 code uses binary32 intermediates for the regression and derived
values. Python normally evaluates floats as binary64, so the port explicitly
rounds after every native `FMUL`, `FDIV`, `FADD`, and `FSUB` equivalent. Native
equivalence was established during development against a private capture that
is intentionally not published. The privacy-safe public fixture locks the port
to these synthetic regression values:

| Field | Synthetic regression value |
| --- | ---: |
| Body fat | 24.537979125976562 kg |
| Body fat | 32.80478286743164% |
| Muscle | 46.65100860595703 kg |
| Muscle | 62.367652893066406% |
| Body water | 36.74589157104492 kg |
| Body water | 49.12552261352539% |
| BMR | 1455 kcal |
| BMI | 24.424489974975586 |

Only these eight fields are exposed by the result model. Other values present
in the native result structure remain outside the integration until their
meaning and runtime output are verified.
