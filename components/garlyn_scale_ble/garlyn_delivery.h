#pragma once

#include <array>
#include <cstddef>
#include <cstdint>
#include <string>
#include <type_traits>

namespace esphome::garlyn_scale_ble {

static constexpr size_t GARLYN_DELIVERY_QUEUE_CAPACITY = 8;
static constexpr size_t GARLYN_MAX_PAYLOAD_SIZE = 640;

enum class EnqueueResult : uint8_t {
  QUEUED,
  INVALID,
  TOO_LARGE,
  FULL,
};

enum class DeliveryDisposition : uint8_t {
  ACKNOWLEDGED,
  RETRY,
};

struct DeliveryQueueState {
  uint32_t magic;
  uint16_t version;
  uint8_t head;
  uint8_t count;
  std::array<uint16_t, GARLYN_DELIVERY_QUEUE_CAPACITY> lengths;
  std::array<std::array<char, GARLYN_MAX_PAYLOAD_SIZE>,
             GARLYN_DELIVERY_QUEUE_CAPACITY>
      payloads;
  uint32_t checksum;
};

static_assert(std::is_trivially_copyable_v<DeliveryQueueState>);

class DeliveryQueue {
 public:
  DeliveryQueue();

  void reset();
  bool restore(const DeliveryQueueState &state);
  EnqueueResult enqueue(const std::string &payload);
  bool front(std::string *payload) const;
  bool pop();

  bool empty() const { return this->state_.count == 0; }
  size_t size() const { return this->state_.count; }
  const DeliveryQueueState &state() const { return this->state_; }

 protected:
  static uint32_t calculate_checksum_(const DeliveryQueueState &state);
  static bool validate_(const DeliveryQueueState &state);
  void update_checksum_();

  DeliveryQueueState state_{};
};

DeliveryDisposition classify_http_status(int status_code);
uint32_t delivery_preference_key(const std::string &scale_id);

}  // namespace esphome::garlyn_scale_ble
