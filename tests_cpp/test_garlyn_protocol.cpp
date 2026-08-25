#include "components/garlyn_scale_ble/garlyn_protocol.h"

#include <array>
#include <cassert>
#include <cmath>
#include <cstdint>
#include <cstring>
#include <iostream>
#include <string>

namespace garlyn = esphome::garlyn_scale_ble;

namespace {

// Deliberately synthetic protocol frame. PIN, weight, BIA, and checksum do not
// originate from a person or physical scale capture.
constexpr std::array<uint8_t, garlyn::GARLYN_FRAME_SIZE> SYNTHETIC_FRAME{
    0x33, 0xCC, 0x44, 0x00, 0x01, 0x03, 0x81, 0x01, 0x42, 0x42, 0x1D, 0x38,
    0x40, 0x00, 0x02, 0x01, 0x02, 0x00, 0x00, 0x00, 0x00, 0x00, 0x2A, 0x04,
    0x60, 0x00, 0x45, 0x7F, 0x30, 0x00, 0x45, 0x80, 0x80, 0x00, 0x43, 0x96,
    0x70, 0x00, 0x45, 0x5E, 0x40, 0x00, 0x45, 0x61, 0x60, 0x00, 0x45, 0x63,
    0x30, 0x00, 0x45, 0x64, 0x80, 0x00, 0x43, 0x84, 0x20, 0x00, 0x45, 0x43,
    0x40, 0x00, 0x45, 0x45, 0x03, 0x03, 0x00, 0x21,
};

bool close_to(float actual, float expected) {
  return std::fabs(actual - expected) < 0.001f;
}

garlyn::DecodedMeasurement decode_synthetic_frame() {
  garlyn::DecodedMeasurement measurement;
  garlyn::DecodeError error = garlyn::DecodeError::NONE;
  assert(garlyn::decode_frame(SYNTHETIC_FRAME.data(), SYNTHETIC_FRAME.size(),
                              &measurement, &error));
  assert(error == garlyn::DecodeError::NONE);
  return measurement;
}

void test_reassembly() {
  garlyn::FrameAssembler assembler;
  std::array<uint8_t, garlyn::GARLYN_FRAME_SIZE> frame{};
  const uint8_t unrelated[] = {0x01, 0x02, 0x03};
  assert(assembler.push_fragment(unrelated, sizeof(unrelated), &frame) ==
         garlyn::FragmentStatus::IGNORED);

  assert(assembler.push_fragment(SYNTHETIC_FRAME.data(), 20, &frame) ==
         garlyn::FragmentStatus::INCOMPLETE);
  assert(assembler.size() == 20);
  assert(assembler.push_fragment(SYNTHETIC_FRAME.data() + 20, 20, &frame) ==
         garlyn::FragmentStatus::INCOMPLETE);
  assert(assembler.push_fragment(SYNTHETIC_FRAME.data() + 40, 20, &frame) ==
         garlyn::FragmentStatus::INCOMPLETE);
  assert(assembler.push_fragment(SYNTHETIC_FRAME.data() + 60, 8, &frame) ==
         garlyn::FragmentStatus::COMPLETE);
  assert(frame == SYNTHETIC_FRAME);
  assert(assembler.size() == 0);

  assert(assembler.push_fragment(SYNTHETIC_FRAME.data(),
                                 SYNTHETIC_FRAME.size(), &frame) ==
         garlyn::FragmentStatus::COMPLETE);
  assert(frame == SYNTHETIC_FRAME);

  const uint8_t control_frame[] = {0x33, 0xCC, 0x0B, 0x00};
  assert(assembler.push_fragment(control_frame, sizeof(control_frame), &frame) ==
         garlyn::FragmentStatus::UNSUPPORTED_FRAME);
}

void test_decode() {
  const auto measurement = decode_synthetic_frame();
  assert(measurement.profile_pin == "4242");
  assert(close_to(measurement.weight_kg, 74.8f));
  assert(close_to(measurement.bia_20khz.left_arm, 410.2f));
  assert(close_to(measurement.bia_20khz.right_arm, 408.6f));
  assert(close_to(measurement.bia_20khz.left_leg, 360.4f));
  assert(close_to(measurement.bia_20khz.right_leg, 355.9f));
  assert(close_to(measurement.bia_20khz.trunk, 30.1f));
  assert(close_to(measurement.bia_100khz.left_arm, 365.1f));
  assert(close_to(measurement.bia_100khz.right_arm, 363.8f));
  assert(close_to(measurement.bia_100khz.left_leg, 315.6f));
  assert(close_to(measurement.bia_100khz.right_leg, 312.2f));
  assert(close_to(measurement.bia_100khz.trunk, 26.5f));
  assert(garlyn::frame_fingerprint(SYNTHETIC_FRAME.data(),
                                   SYNTHETIC_FRAME.size()) == 0xFD6B0C48U);
}

void test_validation() {
  auto invalid = SYNTHETIC_FRAME;
  invalid[67] ^= 0x01U;
  garlyn::DecodedMeasurement measurement;
  garlyn::DecodeError error = garlyn::DecodeError::NONE;
  assert(!garlyn::decode_frame(invalid.data(), invalid.size(), &measurement,
                               &error));
  assert(error == garlyn::DecodeError::CHECKSUM);

  invalid = SYNTHETIC_FRAME;
  invalid[8] = 0xFA;
  uint8_t checksum = 1;
  for (size_t index = 0; index < invalid.size() - 1; index++) {
    checksum = static_cast<uint8_t>(checksum + invalid[index]);
  }
  invalid[67] = checksum;
  assert(!garlyn::decode_frame(invalid.data(), invalid.size(), &measurement,
                               &error));
  assert(error == garlyn::DecodeError::PROFILE_PIN);
}

std::string build_synthetic_json() {
  const auto measurement = decode_synthetic_frame();
  std::string payload;
  assert(garlyn::build_transport_json(
      measurement, "synthetic_scale_1", "synthetic-measurement-001",
      "2026-01-15T12:00:00Z", &payload));
  const std::string expected =
      "{\"protocol_version\":1,\"scale_id\":\"synthetic_scale_1\","
      "\"measurement_id\":\"synthetic-measurement-001\","
      "\"measured_at\":\"2026-01-15T12:00:00Z\",\"profile_pin\":\"4242\","
      "\"weight_kg\":74.80,\"bia\":{\"20khz\":[410.200,408.600,360.400,"
      "355.900,30.100],\"100khz\":[365.100,363.800,315.600,312.200,"
      "26.500]}}";
  assert(payload == expected);
  return payload;
}

}  // namespace

int main(int argc, char **argv) {
  test_reassembly();
  test_decode();
  test_validation();
  const std::string payload = build_synthetic_json();
  if (argc == 2 && std::strcmp(argv[1], "--print-json") == 0) {
    std::cout << payload << '\n';
  }
  return 0;
}
