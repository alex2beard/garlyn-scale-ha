"""Host-side regression tests for the ESPHome protocol decoder."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from custom_components.garlyn_scale.transport import parse_measurement

ROOT = Path(__file__).parents[1]


@pytest.fixture(scope="module")
def decoder_test_binary(tmp_path_factory: pytest.TempPathFactory) -> Path:
    compiler = shutil.which("g++")
    if compiler is None:
        pytest.skip("g++ is required for the host-side ESPHome decoder test")
    binary = tmp_path_factory.mktemp("garlyn_cpp") / "test_garlyn_protocol"
    subprocess.run(
        [
            compiler,
            "-std=c++17",
            "-Wall",
            "-Wextra",
            "-Werror",
            "-pedantic",
            f"-I{ROOT}",
            str(ROOT / "components" / "garlyn_scale_ble" / "garlyn_protocol.cpp"),
            str(ROOT / "tests_cpp" / "test_garlyn_protocol.cpp"),
            "-o",
            str(binary),
        ],
        check=True,
        cwd=ROOT,
    )
    return binary


def test_cpp_protocol_regressions(decoder_test_binary: Path) -> None:
    subprocess.run([str(decoder_test_binary)], check=True, cwd=ROOT)


def test_cpp_json_is_accepted_by_ha_transport(decoder_test_binary: Path) -> None:
    completed = subprocess.run(
        [str(decoder_test_binary), "--print-json"],
        check=True,
        capture_output=True,
        text=True,
        cwd=ROOT,
    )
    payload = json.loads(completed.stdout)
    measurement = parse_measurement(payload, expected_scale_id="synthetic_scale_1")
    assert measurement.profile_pin == "4242"
    assert measurement.weight_kg == pytest.approx(74.8)
    assert measurement.bia_20khz.as_tuple() == pytest.approx(
        (410.2, 408.6, 360.4, 355.9, 30.1)
    )
    assert measurement.bia_100khz.as_tuple() == pytest.approx(
        (365.1, 363.8, 315.6, 312.2, 26.5)
    )
