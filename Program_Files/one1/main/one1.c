#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/semphr.h"
#include "freertos/timers.h"
#include "esp_wifi.h"
#include "esp_event.h"
#include "nvs_flash.h"
#include "esp_netif.h"
#include "esp_log.h"
#include "driver/uart.h"
#include <math.h>
#include <stdlib.h>
#include <string.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdarg.h>

// ================= CONFIG =================
#define CONFIG_WIFI_SSID "TP-Link_4732"
#define CONFIG_WIFI_PASS "97187646"

#define WIFI_MAX_RETRY      10
#define WIFI_RETRY_BASE_MS  500

#define SUBCARRIERS         52
#define SAMPLES             400
#define FRAME_SIZE          (SUBCARRIERS * SAMPLES)

// Frame: "START"[LEN_HI][LEN_LO][payload x FRAME_SIZE][CRC_HI][CRC_LO]"END"
#define FRAME_START_STR     "START"
#define FRAME_START_LEN     5
#define FRAME_END_STR       "END"
#define FRAME_END_LEN       3

#define UART_PORT_NUM       UART_NUM_0
#define UART_TX_BUF_SIZE    (FRAME_SIZE + 128)

static const char *TAG = "CSI_MAIN";

// ================= SHARED STATE =================

static uint8_t           frame_buffer[SAMPLES][SUBCARRIERS];
static int               sample_index  = 0;
static bool              frame_ready   = false;
static SemaphoreHandle_t frame_mutex   = NULL;
static SemaphoreHandle_t uart_mutex    = NULL;

// WiFi reconnect — accessed only from the event task, no mutex needed
static int               wifi_retry_count = 0;
static TimerHandle_t     wifi_retry_timer = NULL;

// ================= CRC-16/CCITT =================

static uint16_t crc16(const uint8_t *data, size_t len)
{
    uint16_t crc = 0xFFFF;
    for (size_t i = 0; i < len; i++) {
        crc ^= (uint16_t)data[i] << 8;
        for (int b = 0; b < 8; b++)
            crc = (crc & 0x8000) ? (crc << 1) ^ 0x1021 : (crc << 1);
    }
    return crc;
}

// ================= HAMPEL FILTER =================

static int cmp_int(const void *a, const void *b)
{
    return *(int*)a - *(int*)b;
}

static void hampel_filter(int *data, int len)
{
    const int   k         = 3;
    const float threshold = 3.0f;
    const float k_scale   = 1.4826f;

    for (int i = k; i < len - k; i++) {
        int window[7];
        for (int j = -k; j <= k; j++)
            window[j + k] = data[i + j];

        qsort(window, 7, sizeof(int), cmp_int);
        int median = window[3];

        int devs[7];
        for (int j = 0; j < 7; j++)
            devs[j] = abs(window[j] - median);
        qsort(devs, 7, sizeof(int), cmp_int);

        float mad = k_scale * (float)devs[3];
        if (mad < 1.0f) mad = 1.0f;

        if ((float)abs(data[i] - median) > threshold * mad)
            data[i] = median;
    }
}

// ================= CSI CALLBACK =================

static void wifi_csi_rx_cb(void *ctx, wifi_csi_info_t *info)
{
    if (!info || !info->buf || info->len < 128)
        return;

    int amplitudes[64] = {0};
    int max_pairs = info->len / 2;
    if (max_pairs > 64) max_pairs = 64;

    for (int i = 0; i < max_pairs; i++) {
        int I = (int8_t)info->buf[i * 2];
        int Q = (int8_t)info->buf[i * 2 + 1];
        amplitudes[i] = abs(I) + abs(Q);
    }

    int carriers[SUBCARRIERS];
    int idx = 0;
    for (int i = 6;  i < 32 && idx < SUBCARRIERS; i++) carriers[idx++] = amplitudes[i];
    for (int i = 33; i < 59 && idx < SUBCARRIERS; i++) carriers[idx++] = amplitudes[i];

    hampel_filter(carriers, SUBCARRIERS);

    // Non-blocking take — drop sample rather than stall the radio task
    if (xSemaphoreTake(frame_mutex, 0) != pdTRUE)
        return;

    if (!frame_ready) {
        for (int i = 0; i < SUBCARRIERS; i++) {
            int v = carriers[i];
            frame_buffer[sample_index][i] = (uint8_t)(v > 255 ? 255 : v);
        }
        sample_index++;
        if (sample_index >= SAMPLES) {
            frame_ready  = true;
            sample_index = 0;
        }
    }

    xSemaphoreGive(frame_mutex);
}

// ================= UART TX TASK =================

void uart_tx_task(void *arg)
{
    static uint8_t tx_buf[FRAME_SIZE + 32];

    while (1) {
        bool do_send = false;

        if (xSemaphoreTake(frame_mutex, pdMS_TO_TICKS(20)) == pdTRUE) {
            if (frame_ready) {
                memcpy(tx_buf + FRAME_START_LEN + 2, frame_buffer, FRAME_SIZE);
                frame_ready = false;
                do_send = true;
            }
            xSemaphoreGive(frame_mutex);
        }

        if (do_send) {
            uint16_t len = (uint16_t)FRAME_SIZE;
            uint16_t crc = crc16(tx_buf + FRAME_START_LEN + 2, FRAME_SIZE);

            memcpy(tx_buf, FRAME_START_STR, FRAME_START_LEN);
            tx_buf[FRAME_START_LEN]     = (len >> 8) & 0xFF;
            tx_buf[FRAME_START_LEN + 1] =  len       & 0xFF;
            
            tx_buf[FRAME_START_LEN + 2 + FRAME_SIZE]     = (crc >> 8) & 0xFF;
            tx_buf[FRAME_START_LEN + 2 + FRAME_SIZE + 1] =  crc       & 0xFF;
            memcpy(tx_buf + FRAME_START_LEN + 2 + FRAME_SIZE + 2, FRAME_END_STR, FRAME_END_LEN);

            size_t total_len = FRAME_START_LEN + 2 + FRAME_SIZE + 2 + FRAME_END_LEN;
            xSemaphoreTake(uart_mutex, portMAX_DELAY);
            uart_write_bytes(UART_PORT_NUM, (const char *)tx_buf, total_len);
            xSemaphoreGive(uart_mutex);
            
            ESP_LOGI(TAG, "Frame sent, crc=0x%04X", crc);
        }

        // Explicit yield — IDLE task must always get CPU time or watchdog fires
        vTaskDelay(pdMS_TO_TICKS(5));
    }
}

// ================= WIFI RETRY TIMER CALLBACK =================
// Runs in the timer daemon task — safe to call esp_wifi_connect() here

static void wifi_retry_timer_cb(TimerHandle_t xTimer)
{
    ESP_LOGI(TAG, "Retrying WiFi connection...");
    esp_wifi_connect();
}

// ================= WIFI EVENT HANDLER =================
// !! NEVER call vTaskDelay() here !!
// This runs on the system event task. Blocking it starves IDLE -> watchdog.
// All delays must go through xTimerStart() instead.

static void event_handler(
    void            *arg,
    esp_event_base_t event_base,
    int32_t          event_id,
    void            *event_data)
{
    if (event_base == WIFI_EVENT && event_id == WIFI_EVENT_STA_START) {
        ESP_LOGI(TAG, "WiFi STA started — connecting...");
        esp_wifi_connect();
    }
    else if (event_base == WIFI_EVENT && event_id == WIFI_EVENT_STA_DISCONNECTED) {
        wifi_event_sta_disconnected_t *disc =
            (wifi_event_sta_disconnected_t *)event_data;

        // Log the reason code — helps diagnose wrong password (reason=15),
        // AP not found (reason=201), etc.
        ESP_LOGW(TAG, "Disconnected, reason=%d", disc->reason);

        if (wifi_retry_count < WIFI_MAX_RETRY) {
            // Exponential backoff via timer — never blocks the event task
            uint32_t delay_ms = WIFI_RETRY_BASE_MS * (1u << wifi_retry_count);
            if (delay_ms > 16000) delay_ms = 16000;

            ESP_LOGW(TAG, "Retry %d/%d in %lu ms",
                     wifi_retry_count + 1, WIFI_MAX_RETRY,
                     (unsigned long)delay_ms);

            xTimerChangePeriod(wifi_retry_timer, pdMS_TO_TICKS(delay_ms), 0);
            xTimerStart(wifi_retry_timer, 0);
            wifi_retry_count++;
        } else {
            ESP_LOGE(TAG, "Max retries reached.");
            ESP_LOGE(TAG, "Common causes:");
            ESP_LOGE(TAG, "  reason=15 -> wrong password");
            ESP_LOGE(TAG, "  reason=201 -> SSID not found (check 2.4GHz band)");
            ESP_LOGE(TAG, "  reason=2   -> AP rejected — auth mode mismatch");
            // Leave running so you can read the log — do not restart blindly
        }
    }
    else if (event_base == IP_EVENT && event_id == IP_EVENT_STA_GOT_IP) {
        ip_event_got_ip_t *ev = (ip_event_got_ip_t *)event_data;
        ESP_LOGI(TAG, "Connected! IP: " IPSTR, IP2STR(&ev->ip_info.ip));
        wifi_retry_count = 0;
        xTimerStop(wifi_retry_timer, 0);

        // Enable CSI only after IP is assigned
        wifi_csi_config_t csi_cfg = {
            .lltf_en           = true,
            .htltf_en          = true,
            .stbc_htltf2_en    = true,
            .ltf_merge_en      = true,
            .channel_filter_en = true,
            .manu_scale        = false
        };
        ESP_ERROR_CHECK(esp_wifi_set_csi_config(&csi_cfg));
        ESP_ERROR_CHECK(esp_wifi_set_csi_rx_cb(wifi_csi_rx_cb, NULL));
        ESP_ERROR_CHECK(esp_wifi_set_csi(true));
        ESP_LOGI(TAG, "CSI started — ping this device now");
    }
}

// ================= WIFI INIT =================

static void wifi_init(void)
{
    ESP_ERROR_CHECK(esp_netif_init());
    ESP_ERROR_CHECK(esp_event_loop_create_default());
    esp_netif_create_default_wifi_sta();

    wifi_init_config_t cfg = WIFI_INIT_CONFIG_DEFAULT();
    ESP_ERROR_CHECK(esp_wifi_init(&cfg));

    ESP_ERROR_CHECK(esp_event_handler_instance_register(
        WIFI_EVENT, ESP_EVENT_ANY_ID, event_handler, NULL, NULL));
    ESP_ERROR_CHECK(esp_event_handler_instance_register(
        IP_EVENT, IP_EVENT_STA_GOT_IP, event_handler, NULL, NULL));

    wifi_config_t wifi_config = {
        .sta = {
            .ssid        = CONFIG_WIFI_SSID,
            .password    = CONFIG_WIFI_PASS,
            // No authmode restriction — connects to WPA/WPA2/WPA3/mixed
            .scan_method = WIFI_FAST_SCAN,
            .sort_method = WIFI_CONNECT_AP_BY_SIGNAL,
            .pmf_cfg     = { .capable = true, .required = false },
        }
    };

    ESP_ERROR_CHECK(esp_wifi_set_mode(WIFI_MODE_STA));
    ESP_ERROR_CHECK(esp_wifi_set_config(WIFI_IF_STA, &wifi_config));
    ESP_ERROR_CHECK(esp_wifi_start());
    ESP_ERROR_CHECK(esp_wifi_set_ps(WIFI_PS_NONE));
}

// ================= UART INIT =================
//
// UART0 (GPIO1 TX / GPIO3 RX) — binary frames and logs to Python.

static int log_to_uart0(const char *fmt, va_list args)
{
    // If called from an ISR context, drop the log.
    // Taking a mutex or using blocking FreeRTOS APIs in an ISR will cause a crash.
    // This also prevents a double-exception if the system panics and tries to log.
    if (xPortInIsrContext()) {
        return 0; 
    }

    char buf[256];
    int len = vsnprintf(buf, sizeof(buf), fmt, args);
    
    // vsnprintf returns the number of characters that WOULD have been written
    if (len >= sizeof(buf)) {
        len = sizeof(buf) - 1;
    }

    if (len > 0) {
        if (uart_mutex) {
            // Take mutex with a small timeout. If the frame TX task holds it,
            // we drop the text log. This prevents high-priority WiFi tasks from
            // being blocked indefinitely and avoids corrupting the binary frame.
            if (xSemaphoreTake(uart_mutex, pdMS_TO_TICKS(10)) == pdTRUE) {
                uart_write_bytes(UART_PORT_NUM, buf, (size_t)len);
                xSemaphoreGive(uart_mutex);
            }
        } else {
            uart_write_bytes(UART_PORT_NUM, buf, (size_t)len);
        }
    }
    return len;
}

static void uart_init(void)
{
    // --- UART0: binary frame data and logs at 921600 ---
    uart_config_t data_cfg = {
        .baud_rate  = 921600,
        .data_bits  = UART_DATA_8_BITS,
        .parity     = UART_PARITY_DISABLE,
        .stop_bits  = UART_STOP_BITS_1,
        .flow_ctrl  = UART_HW_FLOWCTRL_DISABLE,
        .source_clk = UART_SCLK_APB,
    };
    ESP_ERROR_CHECK(uart_driver_install(
        UART_PORT_NUM, 256, UART_TX_BUF_SIZE, 0, NULL, 0));
    ESP_ERROR_CHECK(uart_param_config(UART_PORT_NUM, &data_cfg));
    ESP_ERROR_CHECK(uart_set_pin(
        UART_PORT_NUM,
        UART_PIN_NO_CHANGE, UART_PIN_NO_CHANGE,
        UART_PIN_NO_CHANGE, UART_PIN_NO_CHANGE));

    // Route standard ESP_LOGs securely into the driver ringbuffer
    esp_log_set_vprintf(log_to_uart0);
}

// ================= MAIN =================

void app_main(void)
{
    esp_err_t ret = nvs_flash_init();
    if (ret == ESP_ERR_NVS_NO_FREE_PAGES ||
        ret == ESP_ERR_NVS_NEW_VERSION_FOUND) {
        ESP_ERROR_CHECK(nvs_flash_erase());
        ret = nvs_flash_init();
    }
    ESP_ERROR_CHECK(ret);

    frame_mutex = xSemaphoreCreateMutex();
    configASSERT(frame_mutex != NULL);

    uart_mutex = xSemaphoreCreateMutex();
    configASSERT(uart_mutex != NULL);

    // One-shot timer — fires once per retry, not a repeating loop
    wifi_retry_timer = xTimerCreate(
        "wifi_retry",
        pdMS_TO_TICKS(WIFI_RETRY_BASE_MS),
        pdFALSE,
        NULL,
        wifi_retry_timer_cb
    );
    configASSERT(wifi_retry_timer != NULL);

    uart_init();
    wifi_init();

    xTaskCreate(uart_tx_task, "uart_tx", 4096, NULL, 5, NULL);

    ESP_LOGI(TAG, "System started. Waiting for WiFi...");
}