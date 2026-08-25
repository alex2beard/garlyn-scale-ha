#pragma once

#include <array>
#include <cstddef>
#include <cstdint>
#include <string>

namespace esphome::garlyn_scale_ble {

static constexpr size_t GARLYN_FRAME_SIZE = 68;

struct SegmentalImpedance {
  float left_arm;
  float right_arm;
  float left_leg;
  float right_leg;
  float trunk;
};

struct DecodedMeasurement {
  std::string profile_pin;
  float weight_kg;
  SegmentalImpedance bia_20khz;
  SegmentalImpedance bia_100khz;
};

enum class FragmentStatus : uint8_t {
  IGNORED,
  INCOMPLETE,
  COMPLETE,
  UNSUPPORTED_FRAME,
  OVERFLOW,
};

enum class DecodeError : uint8_t {
  NONE,
  LENGTH,
  HEADER,
  CHECKSUM,
  PROFILE_PIN,
  WEIGHT,
  IMPEDANCE,
};

class FrameAssembler {
 public:
  FragmentStatus push_fragment(const uint8_t *data, size_t length,
                               std::array<uint8_t, GARLYN_FRAME_SIZE> *frame);
  void reset();
  size_t size() const { return this->size_; }

 protected:
  std::array<uint8_t, GARLYN_FRAME_SIZE> buffer_{};
  size_t size_{0};
  bool active_{false};
};

bool decode_frame(const uint8_t *frame, size_t length,
                  DecodedMeasurement *measurement, DecodeError *error = nullptr);
const char *decode_error_to_string(DecodeError error);

uint32_t frame_fingerprint(const uint8_t *frame, size_t length);

bool build_transport_json(const DecodedMeasurement &measurement,
                          const std::string &scale_id,
                          const std::string &measurement_id,
                          const std::string &measured_at, std::string *payload);

}  // namespace esphome::garlyn_scale_ble
