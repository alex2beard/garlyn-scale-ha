# MovingLife profile semantics

Evidence source: MovingLife Android application `com.moving.movinglife` 5.12.9
(version code 204) and its ARM64 `libBodyFatScaleAlg.so`.

APK SHA-256:

```text
69bfa7c6b2d46fc12e76f34c20d2cd743d3d1bad92e2f98501fd4ca6cf82a870
```

This document records reverse-engineering facts without committing the APK or
vendor native libraries.

## JNI input

`com.example.jnipack.Info` contains:

```text
String bhNationality
int    bhSex
int    bhAge
float  bhHeightCm
float  bhWeightKg
int    bhActivityLevel
```

The native validation accepts activity values `0..5`; invalid values produce
error `-18`.

## Activity

Legacy application resources preserve the following picker order:

| Value | Label |
| ---: | --- |
| 1 | Sedentary |
| 2 | Light activity |
| 3 | Moderate activity |
| 4 | Very active |
| 5 | Extremely active |

The current profile screen does not invoke that picker. It displays an
**Athlete Mode** switch and saves:

```text
off -> 0
on  -> 5
```

Native AMR factors are:

```text
0 -> male 1.54, female 1.32
1 -> 1.20
2 -> 1.375
3 -> 1.55
4 -> 1.725
5 -> 1.90
```

For algorithm type 2 (`bodyFatScaleAlg_2.2.2`), activity is read for AMR only.
It does not affect the verified fat, muscle, water, BMR, or numeric BMI outputs.

## Reference standard (`bhNationality`)

The direct MovingLife calculation path assigns:

```text
foreign == false -> "internal"
foreign == true  -> "external"
```

The global `foreign` value follows the logged-in user's server region. Without a
logged-in user, the app selects China only when the time-zone display name is
`GMT+08:00` and the locale language indicates Chinese; other environments select
`external`.

An alternate SDK path maps country code 156 (China) to `internal` and other
codes to `external`. A separate history helper contains an inconsistent mapping
and is not authoritative for the live GARLYN path.

The native library uses exact string comparisons:

- `internal`: adult BMI category thresholds 24 and 28;
- `external`: adult BMI category thresholds 25 and 30;
- unknown strings fall back to `internal`.

The selector affects reference ranges/classifications, body type, and body
score. It does not change the raw BMI number or the verified fat, muscle, water,
and BMR numbers.

## Public evidence handling

Personal profile settings, application-database records, and captured biometric
measurements are not included in this repository. The public regression fixture
is explicitly synthetic. The Athlete Mode and reference-standard mappings above
come from application behavior and native-code analysis, not from a published
user profile.
