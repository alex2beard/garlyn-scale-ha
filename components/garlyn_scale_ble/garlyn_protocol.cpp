#include "garlyn_protocol.h"

#include <cmath>
#include <cstdio>
#include <cstring>
#include <utility>

namespace esphome::garlyn_scale_ble {

namespace {

constexpr uint8_t FRAME_MAGIC_0 = 0x33;
constexpr uint8_t FRAME_MAGIC_1 = 0xCC;
constexpr float MAX_WEIGHT_KG = 500.0f;
constexpr float MAX_IMPEDANCE_OHM = 10000.0f;

bool starts_frame(const uint8_t *data, size_t length) {
  return length >= 3 && data[0] == FRAME_MAGIC_0 && data[1] == FRAME_MAGIC_1;
}

uint32_t read_be32(const uint8_t *data) {
  return (static_cast<uint32_t>(data[0]) << 24U) |
         (static_cast<uint32_t>(data[1]) << 16U) |
         (static_cast<uint32_t>(data[2]) << 8U) |
         static_cast<uint32_t>(data[3]);
}

float decode_impedance(const uint8_t *data) {
  const uint32_t encoded = read_be32(data);
  const uint32_t float_bits = (encoded << 16U) | (encoded >> 16U);
  float value = 0.0f;
  static_assert(sizeof(value) == sizeof(float_bits));
  std::memcpy(&value, &float_bits, sizeof(value));
  return value / 10.0f;
}

bool valid_impedance(float value) {
  return std::isfinite(value) && value > 0.0f && value <= MAX_IMPEDANCE_OHM;
}

bool valid_bcd(uint8_t value) {
  return (value >> 4U) <= 9U && (value & 0x0FU) <= 9U;
}

std::string escape_json_string(const std::string &value) {
  static constexpr char HEX[] = "0123456789abcdef";
  std::string escaped;
  escaped.reserve(value.size());
  for (const unsigned char character : value) {
    switch (character) {
      case '\"':
        escaped += "\\\"";
        break;
      case '\\':
        escaped += "\\\\";
        break;
      case '\b':
        escaped += "\\b";
        break;
      case '\f':
        escaped += "\\f";
        break;
      case '\n':
        escaped += "\\n";
        break;
      case '\r':
        escaped += "\\r";
        break;
      case '\t':
        escaped += "\\t";
        break;
      default:
        if (character < 0x20U) {
          escaped += "\\u00";
          escaped += HEX[character >> 4U];
          escaped += HEX[character & 0x0FU];
        } else {
          escaped += static_cast<char>(character);
        }
    }
  }
  return escaped;
}

void set_error(DecodeError *error, DecodeError value) {
  if (error != nullptr) {
    *error = value;
  }
}

}  // namespace

FragmentStatus FrameAssembler::push_fragment(
    const uint8_t *data, size_t length,
    std::array<uint8_t, GARLYN_FRAME_SIZE> *frame) {
  if (data == nullptr || length == 0) {
    return FragmentStatus::IGNORED;
  }

  if (starts_frame(data, length)) {
    this->reset();
    if (data[2] != GARLYN_FRAME_SIZE) {
      return FragmentStatus::UNSUPPORTED_FRAME;
    }
    this->active_ = true;
  } else if (!this->active_) {
    return FragmentStatus::IGNORED;
  }

  if (length > GARLYN_FRAME_SIZE - this->size_) {
    this->reset();
    return FragmentStatus::OVERFLOW;
  }

  std::memcpy(this->buffer_.data() + this->size_, data, length);
  this->size_ += length;
  if (this->size_ < GARLYN_FRAME_SIZE) {
    return FragmentStatus::INCOMPLETE;
  }

  if (frame != nullptr) {
    *frame = this->buffer_;
  }
  this->reset();
  return FragmentStatus::COMPLETE;
}

void FrameAssembler::reset() {
  this->size_ = 0;
  this->active_ = false;
}

bool decode_frame(const uint8_t *frame, size_t length,
                  DecodedMeasurement *measurement, DecodeError *error) {
  set_error(error, DecodeError::NONE);
  if (frame == nullptr || measurement == nullptr || length != GARLYN_FRAME_SIZE) {
    set_error(error, DecodeError::LENGTH);
    return false;
  }
  if (frame[0] != FRAME_MAGIC_0 || frame[1] != FRAME_MAGIC_1 ||
      frame[2] != GARLYN_FRAME_SIZE) {
    set_error(error, DecodeError::HEADER);
    return false;
  }

  uint8_t expected_checksum = 1;
  for (size_t index = 0; index < GARLYN_FRAME_SIZE - 1; index++) {
    expected_checksum = static_cast<uint8_t>(expected_checksum + frame[index]);
  }
  if (expected_checksum != frame[GARLYN_FRAME_SIZE - 1]) {
    set_error(error, DecodeError::CHECKSUM);
    return false;
  }

  if (!valid_bcd(frame[8]) || !valid_bcd(frame[9])) {
    set_error(error, DecodeError::PROFILE_PIN);
    return false;
  }
  char profile_pin[5];
  profile_pin[0] = static_cast<char>('0' + (frame[8] >> 4U));
  profile_pin[1] = static_cast<char>('0' + (frame[8] & 0x0FU));
  profile_pin[2] = static_cast<char>('0' + (frame[9] >> 4U));
  profile_pin[3] = static_cast<char>('0' + (frame[9] & 0x0FU));
  profile_pin[4] = '\0';

  const uint16_t raw_weight =
      (static_cast<uint16_t>(frame[10]) << 8U) | frame[11];
  const float weight_kg = static_cast<float>(raw_weight) / 100.0f;
  if (!std::isfinite(weight_kg) || weight_kg <= 0.0f ||
      weight_kg > MAX_WEIGHT_KG) {
    set_error(error, DecodeError::WEIGHT);
    return false;
  }

  // GARLYN packet order is right arm, left arm, trunk, right leg, left leg.
  // The Home Assistant transport order is left arm, right arm, left leg,
  // right leg, trunk.
  const SegmentalImpedance bia_20khz{
      decode_impedance(frame + 28), decode_impedance(frame + 24),
      decode_impedance(frame + 40), decode_impedance(frame + 36),
      decode_impedance(frame + 32),
  };
  const SegmentalImpedance bia_100khz{
      decode_impedance(frame + 48), decode_impedance(frame + 44),
      decode_impedance(frame + 60), decode_impedance(frame + 56),
      decode_impedance(frame + 52),
  };

  const float impedances[] = {
      bia_20khz.left_arm,    bia_20khz.right_arm, bia_20khz.left_leg,
      bia_20khz.right_leg,  bia_20khz.trunk,     bia_100khz.left_arm,
      bia_100khz.right_arm, bia_100khz.left_leg, bia_100khz.right_leg,
      bia_100khz.trunk,
  };
  for (const float impedance : impedances) {
    if (!valid_impedance(impedance)) {
      set_error(error, DecodeError::IMPEDANCE);
      return false;
    }
  }

  measurement->profile_pin = profile_pin;
  measurement->weight_kg = weight_kg;
  measurement->bia_20khz = bia_20khz;
  measurement->bia_100khz = bia_100khz;
  return true;
}

const char *decode_error_to_string(DecodeError error) {
  switch (error) {
    case DecodeError::NONE:
      return "none";
    case DecodeError::LENGTH:
      return "length";
    case DecodeError::HEADER:
      return "header";
    case DecodeError::CHECKSUM:
      return "checksum";
    case DecodeError::PROFILE_PIN:
      return "profile_pin";
    case DecodeError::WEIGHT:
      return "weight";
    case DecodeError::IMPEDANCE:
      return "impedance";
  }
  return "unknown";
}

uint32_t frame_fingerprint(const uint8_t *frame, size_t length) {
  uint32_t hash = 2166136261U;
  if (frame == nullptr) {
    return hash;
  }
  for (size_t index = 0; index < length; index++) {
    hash ^= frame[index];
    hash *= 16777619U;
  }
  return hash;
}

bool build_transport_json(const DecodedMeasurement &measurement,
                          const std::string &scale_id,
                          const std::string &measurement_id,
                          const std::string &measured_at, std::string *payload) {
  if (payload == nullptr || scale_id.empty() || measurement_id.empty() ||
      measured_at.empty()) {
    return false;
  }

  char values[320];
  const int written = std::snprintf(
      values, sizeof(values),
      "\",\"profile_pin\":\"%s\",\"weight_kg\":%.2f,\"bia\":{"
      "\"20khz\":[%.3f,%.3f,%.3f,%.3f,%.3f],"
      "\"100khz\":[%.3f,%.3f,%.3f,%.3f,%.3f]}}",
      measurement.profile_pin.c_str(), measurement.weight_kg,
      measurement.bia_20khz.left_arm, measurement.bia_20khz.right_arm,
      measurement.bia_20khz.left_leg, measurement.bia_20khz.right_leg,
      measurement.bia_20khz.trunk, measurement.bia_100khz.left_arm,
      measurement.bia_100khz.right_arm, measurement.bia_100khz.left_leg,
      measurement.bia_100khz.right_leg, measurement.bia_100khz.trunk);
  if (written < 0 || static_cast<size_t>(written) >= sizeof(values)) {
    return false;
  }

  std::string result = "{\"protocol_version\":1,\"scale_id\":\"";
  result += escape_json_string(scale_id);
  result += "\",\"measurement_id\":\"";
  result += escape_json_string(measurement_id);
  result += "\",\"measured_at\":\"";
  result += escape_json_string(measured_at);
  result += values;
  *payload = std::move(result);
  return true;
}

}  // namespace esphome::garlyn_scale_ble
