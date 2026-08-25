#include "garlyn_scale_ble.h"

#ifdef USE_ESP32

#include "esphome/components/esp32_ble_tracker/esp32_ble_tracker.h"
#include "esphome/core/log.h"

#include <cinttypes>
#include <cstdio>

namespace esphome::garlyn_scale_ble {

namespace espbt = esphome::esp32_ble_tracker;

static const char *const TAG = "garlyn_scale_ble";
static constexpr uint16_t GARLYN_SERVICE_UUID = 0xFFF0;
static constexpr uint16_t GARLYN_RESULT_UUID = 0xFFF3;

void GarlynScaleBle::dump_config() {
  ESP_LOGCONFIG(TAG, "GARLYN scale BLE decoder");
  ESP_LOGCONFIG(TAG, "  Scale ID: %s", this->scale_id_.c_str());
  ESP_LOGCONFIG(TAG, "  Service/characteristic: FFF0/FFF3");
  ESP_LOGCONFIG(TAG, "  Delivery: log only (webhook disabled)");
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
  ESP_LOGI(TAG, "transport_v1: %s", payload.c_str());
}

}  // namespace esphome::garlyn_scale_ble

#endif  // USE_ESP32
