#pragma once

#include "esphome/components/ble_client/ble_client.h"
#include "esphome/components/http_request/http_request.h"
#include "esphome/components/time/real_time_clock.h"
#include "esphome/core/component.h"
#include "esphome/core/preferences.h"
#include "garlyn_delivery.h"
#include "garlyn_protocol.h"

#ifdef USE_ESP32

#include <esp_gattc_api.h>

#include <cstdint>
#include <string>

namespace esphome::garlyn_scale_ble {

class GarlynScaleBle : public Component, public ble_client::BLEClientNode {
 public:
  void setup() override;
  void loop() override;
  void dump_config() override;
  void gattc_event_handler(esp_gattc_cb_event_t event, esp_gatt_if_t gattc_if,
                           esp_ble_gattc_cb_param_t *param) override;

  void set_time(time::RealTimeClock *time) { this->time_ = time; }
  void set_scale_id(const std::string &scale_id) { this->scale_id_ = scale_id; }
  void set_http_request(http_request::HttpRequestComponent *http_request) {
    this->http_request_ = http_request;
  }
  void set_webhook_url(const std::string &webhook_url) {
    this->webhook_url_ = webhook_url;
  }
  void set_retry_interval(uint32_t retry_interval_ms) {
    this->retry_interval_ms_ = retry_interval_ms;
  }

 protected:
  void process_frame_(const std::array<uint8_t, GARLYN_FRAME_SIZE> &frame);
  bool persist_queue_();
  void enqueue_payload_(const std::string &payload);
  void try_deliver_();

  time::RealTimeClock *time_{nullptr};
  http_request::HttpRequestComponent *http_request_{nullptr};
  std::string scale_id_;
  std::string webhook_url_;
  uint16_t fff3_handle_{0};
  FrameAssembler assembler_;
  DeliveryQueue delivery_queue_;
  ESPPreferenceObject delivery_preference_;
  bool delivery_preference_ready_{false};
  uint32_t retry_interval_ms_{60000};
  uint32_t next_delivery_attempt_ms_{0};
};

}  // namespace esphome::garlyn_scale_ble

#endif  // USE_ESP32
