#pragma once

#include "esphome/components/ble_client/ble_client.h"
#include "esphome/components/time/real_time_clock.h"
#include "esphome/core/component.h"
#include "garlyn_protocol.h"

#ifdef USE_ESP32

#include <esp_gattc_api.h>

#include <cstdint>
#include <string>

namespace esphome::garlyn_scale_ble {

class GarlynScaleBle : public Component, public ble_client::BLEClientNode {
 public:
  void dump_config() override;
  void gattc_event_handler(esp_gattc_cb_event_t event, esp_gatt_if_t gattc_if,
                           esp_ble_gattc_cb_param_t *param) override;

  void set_time(time::RealTimeClock *time) { this->time_ = time; }
  void set_scale_id(const std::string &scale_id) { this->scale_id_ = scale_id; }

 protected:
  void process_frame_(const std::array<uint8_t, GARLYN_FRAME_SIZE> &frame);

  time::RealTimeClock *time_{nullptr};
  std::string scale_id_;
  uint16_t fff3_handle_{0};
  FrameAssembler assembler_;
};

}  // namespace esphome::garlyn_scale_ble

#endif  // USE_ESP32
