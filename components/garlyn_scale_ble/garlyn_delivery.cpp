#include "garlyn_delivery.h"

#include <algorithm>
#include <cstring>

namespace esphome::garlyn_scale_ble {

namespace {

constexpr uint32_t DELIVERY_QUEUE_MAGIC = 0x474C5951U;
constexpr uint16_t DELIVERY_QUEUE_VERSION = 1;
constexpr uint32_t FNV_OFFSET_BASIS = 2166136261U;
constexpr uint32_t FNV_PRIME = 16777619U;

uint32_t fnv1a(const uint8_t *data, size_t length) {
  uint32_t hash = FNV_OFFSET_BASIS;
  for (size_t index = 0; index < length; index++) {
    hash ^= data[index];
    hash *= FNV_PRIME;
  }
  return hash;
}

}  // namespace

DeliveryQueue::DeliveryQueue() { this->reset(); }

void DeliveryQueue::reset() {
  this->state_ = {};
  this->state_.magic = DELIVERY_QUEUE_MAGIC;
  this->state_.version = DELIVERY_QUEUE_VERSION;
  this->update_checksum_();
}

bool DeliveryQueue::restore(const DeliveryQueueState &state) {
  if (!validate_(state)) {
    this->reset();
    return false;
  }
  this->state_ = state;
  return true;
}

EnqueueResult DeliveryQueue::enqueue(const std::string &payload) {
  if (payload.empty()) {
    return EnqueueResult::INVALID;
  }
  if (payload.size() > GARLYN_MAX_PAYLOAD_SIZE) {
    return EnqueueResult::TOO_LARGE;
  }
  if (this->state_.count >= GARLYN_DELIVERY_QUEUE_CAPACITY) {
    return EnqueueResult::FULL;
  }

  const size_t index =
      (this->state_.head + this->state_.count) %
      GARLYN_DELIVERY_QUEUE_CAPACITY;
  auto &slot = this->state_.payloads[index];
  std::fill(slot.begin(), slot.end(), '\0');
  std::memcpy(slot.data(), payload.data(), payload.size());
  this->state_.lengths[index] = static_cast<uint16_t>(payload.size());
  this->state_.count++;
  this->update_checksum_();
  return EnqueueResult::QUEUED;
}

bool DeliveryQueue::front(std::string *payload) const {
  if (payload == nullptr || this->empty()) {
    return false;
  }
  const size_t index = this->state_.head;
  payload->assign(this->state_.payloads[index].data(),
                  this->state_.lengths[index]);
  return true;
}

bool DeliveryQueue::pop() {
  if (this->empty()) {
    return false;
  }
  const size_t index = this->state_.head;
  this->state_.lengths[index] = 0;
  this->state_.payloads[index].fill('\0');
  this->state_.count--;
  if (this->state_.count == 0) {
    this->state_.head = 0;
  } else {
    this->state_.head = static_cast<uint8_t>(
        (this->state_.head + 1U) % GARLYN_DELIVERY_QUEUE_CAPACITY);
  }
  this->update_checksum_();
  return true;
}

uint32_t DeliveryQueue::calculate_checksum_(const DeliveryQueueState &state) {
  return fnv1a(reinterpret_cast<const uint8_t *>(&state),
               offsetof(DeliveryQueueState, checksum));
}

bool DeliveryQueue::validate_(const DeliveryQueueState &state) {
  if (state.magic != DELIVERY_QUEUE_MAGIC ||
      state.version != DELIVERY_QUEUE_VERSION ||
      state.head >= GARLYN_DELIVERY_QUEUE_CAPACITY ||
      state.count > GARLYN_DELIVERY_QUEUE_CAPACITY ||
      calculate_checksum_(state) != state.checksum) {
    return false;
  }
  for (size_t offset = 0; offset < state.count; offset++) {
    const size_t index =
        (state.head + offset) % GARLYN_DELIVERY_QUEUE_CAPACITY;
    if (state.lengths[index] == 0 ||
        state.lengths[index] > GARLYN_MAX_PAYLOAD_SIZE) {
      return false;
    }
  }
  return true;
}

void DeliveryQueue::update_checksum_() {
  this->state_.checksum = calculate_checksum_(this->state_);
}

DeliveryDisposition classify_http_status(int status_code) {
  if (status_code == 200 || status_code == 202) {
    return DeliveryDisposition::ACKNOWLEDGED;
  }
  return DeliveryDisposition::RETRY;
}

uint32_t delivery_preference_key(const std::string &scale_id) {
  static constexpr char PREFIX[] = "garlyn_scale_ble.delivery.v1:";
  uint32_t hash = fnv1a(reinterpret_cast<const uint8_t *>(PREFIX),
                        sizeof(PREFIX) - 1U);
  for (const unsigned char character : scale_id) {
    hash ^= character;
    hash *= FNV_PRIME;
  }
  return hash;
}

}  // namespace esphome::garlyn_scale_ble
