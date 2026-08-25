#include "garlyn_scale_ble.h"

#ifdef USE_ESP32

#include "esphome/components/esp32_ble_tracker/esp32_ble_tracker.h"
#include "esphome/core/log.h"

#include <cinttypes>
#include <cstdio>
#include <memory>
#include <new>

namespace esphome::garlyn_scale_ble {

namespace espbt = esphome::esp32_ble_tracker;

static const char *const TAG = "garlyn_scale_ble";
static constexpr uint16_t GARLYN_SERVICE_UUID = 0xFFF0;
static constexpr uint16_t GARLYN_RESULT_UUID = 0xFFF3;
static constexpr uint32_t INITIAL_DELIVERY_DELAY_MS = 5000;
static constexpr uint32_t NEXT_QUEUE_ITEM_DELAY_MS = 1000;

void GarlynScaleBle::setup() {
  this->next_delivery_attempt_ms_ = millis() + INITIAL_DELIVERY_DELAY_MS;
  if (global_preferences == nullptr) {
    this->status_set_warning();
    ESP_LOGW(TAG, "Persistent delivery queue is unavailable");
    return;
  }

  this->delivery_preference_ =
      global_preferences->make_preference<DeliveryQueueState>(
          delivery_preference_key(this->scale_id_));
  this->delivery_preference_ready_ = true;

  std::unique_ptr<DeliveryQueueState> stored(
      new (std::nothrow) DeliveryQueueState{});
  if (stored == nullptr) {
    this->status_set_warning();
    ESP_LOGW(TAG, "Could not allocate the persistent queue restore buffer");
    return;
  }
  if (!this->delivery_preference_.load(stored.get())) {
    ESP_LOGD(TAG, "No persistent GARLYN measurements to restore");
    return;
  }
  if (!this->delivery_queue_.restore(*stored)) {
    this->status_set_warning();
    ESP_LOGW(TAG, "Discarded invalid persistent delivery queue");
    this->persist_queue_();
    return;
  }
  if (!this->delivery_queue_.empty()) {
    ESP_LOGI(TAG, "Restored %u pending GARLYN measurement(s)",
             static_cast<unsigned>(this->delivery_queue_.size()));
  }
}

void GarlynScaleBle::loop() {
  if (this->delivery_queue_.empty()) {
    return;
  }
  const uint32_t now = millis();
  if (static_cast<int32_t>(now - this->next_delivery_attempt_ms_) < 0) {
    return;
  }
  this->next_delivery_attempt_ms_ = now + this->retry_interval_ms_;
  this->try_deliver_();
}

void GarlynScaleBle::dump_config() {
  ESP_LOGCONFIG(TAG, "GARLYN scale BLE decoder");
  ESP_LOGCONFIG(TAG, "  Scale ID: %s", this->scale_id_.c_str());
  ESP_LOGCONFIG(TAG, "  Service/characteristic: FFF0/FFF3");
  ESP_LOGCONFIG(TAG, "  Delivery: persistent HTTP webhook queue");
  ESP_LOGCONFIG(TAG, "  Webhook configured: %s",
                YESNO(!this->webhook_url_.empty()));
  ESP_LOGCONFIG(TAG, "  Retry interval: %u s",
                static_cast<unsigned>(this->retry_interval_ms_ / 1000U));
  ESP_LOGCONFIG(TAG, "  Pending measurements: %u/%u",
                static_cast<unsigned>(this->delivery_queue_.size()),
                static_cast<unsigned>(GARLYN_DELIVERY_QUEUE_CAPACITY));
}

void GarlynScaleBle::gattc_event_handler(esp_gattc_cb_event_t event,
                                         esp_gatt_if_t gattc_if,
                                         esp_ble_gattc_cb_param_t *param) {
  (void) gattc_if;
  switch (event) {
    case ESP_GATTC_SEARCH_CMPL_EVT: {
      this->fff3_handle_ = 0;
      this->assembler_.reset();
      auto *characteristic = this->parent()->get_characteristic(
          espbt::ESPBTUUID::from_uint16(GARLYN_SERVICE_UUID),
          espbt::ESPBTUUID::from_uint16(GARLYN_RESULT_UUID));
      if (characteristic == nullptr) {
        this->status_set_warning();
        ESP_LOGW(TAG, "FFF3 result characteristic not found");
        break;
      }

      this->fff3_handle_ = characteristic->handle;
      const auto status = esp_ble_gattc_register_for_notify(
          this->parent()->get_gattc_if(), this->parent()->get_remote_bda(),
          this->fff3_handle_);
      if (status != ESP_OK) {
        this->status_set_warning();
        ESP_LOGW(TAG, "Failed to subscribe to FFF3, status=%d", status);
      }
      break;
    }
    case ESP_GATTC_REG_FOR_NOTIFY_EVT: {
      if (param->reg_for_notify.handle != this->fff3_handle_) {
        break;
      }
      if (param->reg_for_notify.status != ESP_GATT_OK) {
        this->status_set_warning();
        ESP_LOGW(TAG, "FFF3 subscription failed, status=%d",
                 param->reg_for_notify.status);
        break;
      }
      this->node_state = espbt::ClientState::ESTABLISHED;
      this->status_clear_warning();
      ESP_LOGI(TAG, "FFF3 indication subscription is ready");
      break;
    }
    case ESP_GATTC_NOTIFY_EVT: {
      if (param->notify.handle != this->fff3_handle_ ||
          param->notify.value_len == 0) {
        break;
      }
      std::array<uint8_t, GARLYN_FRAME_SIZE> frame{};
      const FragmentStatus status = this->assembler_.push_fragment(
          param->notify.value, param->notify.value_len, &frame);
      switch (status) {
        case FragmentStatus::COMPLETE:
          this->process_frame_(frame);
          break;
        case FragmentStatus::UNSUPPORTED_FRAME:
          ESP_LOGV(TAG, "Ignoring non-measurement FFF3 frame");
          break;
        case FragmentStatus::OVERFLOW:
          this->status_set_warning();
          ESP_LOGW(TAG, "FFF3 measurement exceeded %u bytes",
                   static_cast<unsigned>(GARLYN_FRAME_SIZE));
          break;
        case FragmentStatus::IGNORED:
        case FragmentStatus::INCOMPLETE:
          break;
      }
      break;
    }
    case ESP_GATTC_CLOSE_EVT:
    case ESP_GATTC_DISCONNECT_EVT:
      this->fff3_handle_ = 0;
      this->assembler_.reset();
      break;
    default:
      break;
  }
}

void GarlynScaleBle::process_frame_(
    const std::array<uint8_t, GARLYN_FRAME_SIZE> &frame) {
  DecodedMeasurement measurement;
  DecodeError error = DecodeError::NONE;
  if (!decode_frame(frame.data(), frame.size(), &measurement, &error)) {
    this->status_set_warning();
    ESP_LOGW(TAG, "Rejected FFF3 measurement: %s",
             decode_error_to_string(error));
    return;
  }

  if (this->time_ == nullptr) {
    this->status_set_warning();
    ESP_LOGW(TAG, "Measurement decoded, but no clock is configured");
    return;
  }
  ESPTime measured_time = this->time_->utcnow();
  if (!measured_time.is_valid()) {
    this->status_set_warning();
    ESP_LOGW(TAG, "Measurement decoded, but time is not synchronized yet");
    return;
  }

  char measured_at[32];
  if (measured_time.strftime(measured_at, sizeof(measured_at),
                             "%Y-%m-%dT%H:%M:%SZ") == 0) {
    this->status_set_warning();
    ESP_LOGW(TAG, "Could not format the measurement timestamp");
    return;
  }

  char measurement_id[112];
  const uint32_t fingerprint =
      frame_fingerprint(frame.data(), frame.size());
  const int id_length = std::snprintf(
      measurement_id, sizeof(measurement_id), "%s-%lld-%08" PRIx32,
      this->scale_id_.c_str(),
      static_cast<long long>(measured_time.timestamp), fingerprint);
  if (id_length < 0 ||
      static_cast<size_t>(id_length) >= sizeof(measurement_id)) {
    this->status_set_warning();
    ESP_LOGW(TAG, "Could not build a bounded measurement ID");
    return;
  }

  std::string payload;
  if (!build_transport_json(measurement, this->scale_id_, measurement_id,
                            measured_at, &payload)) {
    this->status_set_warning();
    ESP_LOGW(TAG, "Could not build transport-v1 JSON");
    return;
  }

  this->status_clear_warning();
  ESP_LOGI(TAG, "Decoded completed measurement for PIN %s: %.2f kg",
           measurement.profile_pin.c_str(), measurement.weight_kg);
  ESP_LOGV(TAG, "transport_v1: %s", payload.c_str());
  this->enqueue_payload_(payload);
}

bool GarlynScaleBle::persist_queue_() {
  if (!this->delivery_preference_ready_ || global_preferences == nullptr) {
    return false;
  }
  const DeliveryQueueState &state = this->delivery_queue_.state();
  return this->delivery_preference_.save(&state) && global_preferences->sync();
}

void GarlynScaleBle::enqueue_payload_(const std::string &payload) {
  const bool was_empty = this->delivery_queue_.empty();
  const EnqueueResult result = this->delivery_queue_.enqueue(payload);
  if (result != EnqueueResult::QUEUED) {
    this->status_set_warning();
    if (result == EnqueueResult::FULL) {
      ESP_LOGW(TAG, "Delivery queue is full; newest measurement was not queued");
    } else if (result == EnqueueResult::TOO_LARGE) {
      ESP_LOGW(TAG, "Measurement JSON exceeds the persistent queue limit");
    } else {
      ESP_LOGW(TAG, "Refused an invalid measurement JSON payload");
    }
    return;
  }

  if (!this->persist_queue_()) {
    this->status_set_warning();
    ESP_LOGW(TAG, "Measurement is queued in RAM, but NVS persistence failed");
  }
  ESP_LOGI(TAG, "Queued GARLYN measurement for delivery; pending=%u",
           static_cast<unsigned>(this->delivery_queue_.size()));
  if (was_empty) {
    this->next_delivery_attempt_ms_ = millis() + NEXT_QUEUE_ITEM_DELAY_MS;
  }
}

void GarlynScaleBle::try_deliver_() {
  if (this->http_request_ == nullptr || this->webhook_url_.empty()) {
    this->status_set_warning();
    ESP_LOGW(TAG, "Webhook delivery is not configured");
    return;
  }

  std::string payload;
  if (!this->delivery_queue_.front(&payload)) {
    this->status_set_warning();
    ESP_LOGW(TAG, "Could not read the oldest queued measurement");
    return;
  }

  const std::vector<http_request::Header> headers{
      {"Content-Type", "application/json"},
  };
  auto response =
      this->http_request_->post(this->webhook_url_, payload, headers);
  if (response == nullptr) {
    this->status_set_warning();
    ESP_LOGW(TAG, "Webhook is unavailable; measurement remains queued");
    return;
  }

  const int status_code = response->status_code;
  response->end();
  if (classify_http_status(status_code) !=
      DeliveryDisposition::ACKNOWLEDGED) {
    this->status_set_warning();
    ESP_LOGW(TAG,
             "Webhook returned HTTP %d; measurement remains queued for retry",
             status_code);
    return;
  }

  this->delivery_queue_.pop();
  if (!this->persist_queue_()) {
    this->status_set_warning();
    ESP_LOGW(TAG, "Delivery succeeded, but queue acknowledgement was not persisted");
  } else {
    this->status_clear_warning();
  }
  ESP_LOGI(TAG, "Webhook acknowledged measurement with HTTP %d; pending=%u",
           status_code,
           static_cast<unsigned>(this->delivery_queue_.size()));
  if (!this->delivery_queue_.empty()) {
    this->next_delivery_attempt_ms_ = millis() + NEXT_QUEUE_ITEM_DELAY_MS;
  }
}

}  // namespace esphome::garlyn_scale_ble

#endif  // USE_ESP32
