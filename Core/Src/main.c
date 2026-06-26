/* USER CODE BEGIN Header */
/**
  ******************************************************************************
  * @file           : main.c
  * @brief          : Main program body
  ******************************************************************************
  * @attention
  *
  * Copyright (c) 2026 STMicroelectronics.
  * All rights reserved.
  *
  * This software is licensed under terms that can be found in the LICENSE file
  * in the root directory of this software component.
  * If no LICENSE file comes with this software, it is provided AS-IS.
  *
  ******************************************************************************
  */
/* USER CODE END Header */
/* Includes ------------------------------------------------------------------*/
#include "main.h"
#include "i2c.h"
#include "usb_device.h"
#include "usbd_cdc_if.h"
#include "gpio.h"

/* Private includes ----------------------------------------------------------*/
/* USER CODE BEGIN Includes */
#include <stdarg.h>
#include <math.h>
#include <stdio.h>
#include <string.h>

/* USER CODE END Includes */

/* Private typedef -----------------------------------------------------------*/
/* USER CODE BEGIN PTD */
typedef struct
{
  uint32_t red;
  uint32_t ir;
} MAX30102_Sample_t;

typedef struct
{
  float red_dc;
  float ir_dc;
  float red_ac;
  float ir_ac;
  float red_filt;
  float ir_filt;
  float red_rms;
  float ir_rms;
  float finger_score;
  float ratio;
  float hr_peak_bpm;
  float last_peak_value;
  float hr_confirm_candidates[3];
  float red_filt_window[1000];
  float ir_filt_window[1000];
  uint32_t peak_ms[12];
  float hr_history[5];
  uint16_t window_index;
  uint16_t window_count;
  uint8_t peak_count;
  uint8_t hr_history_count;
  uint8_t hr_history_index;
  uint8_t hr_confirm_count;
  uint8_t hr_confirm_index;
  uint8_t hr_confirmed;
  uint32_t finger_start_sample;
  uint32_t last_autocorr_sample;
  uint32_t last_hr_confirm_sample;
  uint32_t last_hr_update_tick_ms;
  uint8_t raw_finger_present;
} PPG_State_t;

typedef struct
{
  uint8_t columns[128];
  float peak_floor;
  float peak_ceil;
  uint8_t streaming;
  uint8_t draining;
  uint8_t dirty;
  uint8_t display_mode;
  uint8_t loading_phase;
  uint8_t active_column_count;
  uint8_t trigger_above_threshold;
  uint8_t last_peak_display_px;
  float last_ir_filt;
  uint32_t last_column_sample;
  uint32_t last_peak_sample;
  uint32_t last_flush_ms;
  uint32_t last_status_anim_ms;
} OLED091_WaveformState_t;

typedef struct
{
  uint32_t loop_count;
  uint32_t loop_gap_max_ms;
  uint32_t max30102_service_calls;
  uint32_t max30102_samples_read;
  uint32_t max30102_fifo_max;
  uint32_t max30102_service_max_ms;
  uint32_t autocorr_service_calls;
  uint32_t autocorr_jobs_done;
  uint32_t autocorr_service_max_ms;
  uint32_t oled091_flush_count;
  uint32_t oled091_flush_max_ms;
  uint32_t oled64_flush_count;
  uint32_t oled64_flush_max_ms;
  uint32_t cdc_write_calls;
  uint32_t cdc_busy_count;
  uint32_t cdc_timeout_count;
  uint32_t cdc_write_max_wait_ms;
  uint32_t cdc_write_total_wait_ms;
} DiagnosticStats_t;

typedef enum
{
  APP_DISPLAY_VITALS = 0U,
  APP_DISPLAY_AF_LIVE = 1U,
  APP_DISPLAY_STRESS = 2U,
  APP_DISPLAY_AF_TEST = 3U
} AppDisplayMode_t;

typedef struct
{
  float values[1000];
  float mean;
  float energy;
  float best_corr;
  float corr;
  uint16_t len;
  uint16_t i;
  uint16_t lag;
  uint16_t min_lag;
  uint16_t max_lag;
  uint16_t best_lag;
  uint32_t sample_count;
  uint8_t phase;
} PPG_AutocorrJob_t;

typedef struct
{
  uint32_t peak_ms[12];
  uint32_t pending_peak_ms;
  float pending_peak_value;
  float last_peak_value;
  uint8_t peak_count;
  uint8_t pending_peak_valid;
} PPG_RobustPeakState_t;

/* USER CODE END PTD */

/* Private define ------------------------------------------------------------*/
/* USER CODE BEGIN PD */
#define SSD1306_I2C_ADDR_7BIT    0x3CU
#define MAX30102_I2C_ADDR_7BIT   0x57U

#define I2C_SCAN_TRIALS          1U
#define I2C_SCAN_TIMEOUT_MS      3U
#define CDC_TX_TIMEOUT_MS        100U
#define USB_ENUM_WAIT_MS         1500U
#define CDC_DEBUG_LOG_TO_USB     0U
#define RAW_STREAM_USB_ENABLE    1U
#define DIAG_STREAM_USB_ENABLE   0U
#define PPG_PROC_STREAM_USB_ENABLE 1U
#define FW_TAG                  "RAW_CSV_PPG_C_V4_WAVEFORM"

#define OLED_I2C_TIMEOUT_MS      50U
#define OLED_DATA_CHUNK_BYTES    64U
#define OLED_WIDTH               128U
#define OLED_096_HEIGHT          64U
#define OLED_091_HEIGHT          32U

#define MAX30102_REG_INTR_STATUS_1      0x00U
#define MAX30102_REG_INTR_STATUS_2      0x01U
#define MAX30102_REG_INTR_ENABLE_1      0x02U
#define MAX30102_REG_INTR_ENABLE_2      0x03U
#define MAX30102_REG_FIFO_WR_PTR        0x04U
#define MAX30102_REG_OVF_COUNTER        0x05U
#define MAX30102_REG_FIFO_RD_PTR        0x06U
#define MAX30102_REG_FIFO_DATA          0x07U
#define MAX30102_REG_FIFO_CONFIG        0x08U
#define MAX30102_REG_MODE_CONFIG        0x09U
#define MAX30102_REG_SPO2_CONFIG        0x0AU
#define MAX30102_REG_LED1_PA            0x0CU
#define MAX30102_REG_LED2_PA            0x0DU
#define MAX30102_REG_PILOT_PA           0x10U
#define MAX30102_REG_PART_ID            0xFFU

#define MAX30102_PART_ID                0x15U
#define MAX30102_SAMPLE_RATE_HZ         100U
#define MAX30102_SAMPLE_INTERVAL_MS     10U
#define MAX30102_MAX_FIFO_SAMPLES       8U
#define MAX30102_FALLBACK_POLL_MS       1000U

#define OLED_VITALS_UPDATE_MS           1000U
#define PPG_WINDOW_SAMPLES              1000U
#define PPG_AUTOCORR_MIN_SAMPLES        600U
#define PPG_PEAK_HISTORY_SIZE           12U
#define PPG_HR_HISTORY_SIZE             5U
#define PPG_FINGER_IR_MIN               10000.0f
#define PPG_MIN_AC_RMS                  10.0f
#define PPG_MIN_SPO2_SCORE              4.0f
#define PPG_DC_ALPHA                    0.02f
#define PPG_FILT_ALPHA                  0.20f
#define PPG_HR_WARMUP_SAMPLES           1500U
#define PPG_SPO2_WARMUP_SAMPLES         2000U
#define PPG_MIN_PEAK_MS                 300U
#define PPG_MAX_PEAK_MS                 1200U
#define PPG_MIN_VALID_HR_BPM            45.0f
#define PPG_MAX_VALID_HR_BPM            190.0f
#define PPG_AUTOCORR_INTERVAL_SAMPLES   100U
#define PPG_AUTOCORR_MIN_CORR           0.20f
#define PPG_AUTOCORR_SERVICE_BUDGET     900U
#define PPG_AUTOCORR_JOB_IDLE           0U
#define PPG_AUTOCORR_JOB_MEAN           1U
#define PPG_AUTOCORR_JOB_ENERGY         2U
#define PPG_AUTOCORR_JOB_LAG            3U
#define PPG_HR_STALE_TIMEOUT_MS         5000U
#define PPG_HR_HOLD_TIMEOUT_MS          12000U
#define PPG_HR_MAX_DISPLAY_JUMP_BPM     25
#define PPG_HR_JUMP_ACCEPT_TIMEOUT_MS   10000U
#define PPG_HR_CONFIRM_SAMPLES          3U
#define PPG_HR_CONFIRM_TOLERANCE_BPM    12.0f
#define PPG_HR_CONFIRM_INTERVAL_SAMPLES 100U
#define PPG_ROBUST_BASE_THRESHOLD_RATIO 0.45f
#define PPG_ROBUST_DYNAMIC_MIN_HR_RATIO 0.58f
#define PPG_ROBUST_RECOVERY_START_HR_RATIO 0.72f
#define PPG_ROBUST_RECOVERY_THRESHOLD_RATIO 0.28f
#define PPG_ROBUST_CANDIDATE_HOLD_MS    140U
#define PPG_PPI_FILTER_MIN_HR_RATIO     0.70f
#define PPG_PPI_FILTER_MAX_HR_RATIO     1.45f
#define PPG_PPI_FILTER_SLOW_MIN_RATIO   0.58f
#define PPG_PPI_FILTER_SLOW_MAX_RATIO   1.65f
#define PPG_PPI_FILTER_FAST_MIN_RATIO   0.64f
#define PPG_PPI_FILTER_FAST_MAX_RATIO   1.38f
#define PPG_PPI_QUALITY_MIN_INTERVALS   6U
#define PPG_PPI_QUALITY_MAX_REJECT_PCT  20U

#define OLED091_WAVEFORM_NONE           0xFFU
#define OLED091_WAVEFORM_MID_Y          18U
#define OLED091_WAVEFORM_MIN_Y          3U
#define OLED091_WAVEFORM_MAX_Y          28U
#define OLED091_WAVEFORM_AMP_Y          12U
#define OLED091_WAVEFORM_PEAK_RANGE_PX  4U
#define OLED091_WAVEFORM_COLUMN_SAMPLES 4U
#define OLED091_WAVEFORM_FLUSH_MS       40U
#define OLED091_WAVEFORM_MAX_ADVANCE_COLUMNS 3U
#define OLED091_STATUS_ANIM_MS          350U
#define OLED091_MODE_NONE               0U
#define OLED091_MODE_WAVEFORM           1U
#define OLED091_MODE_PAUSE              2U
#define OLED091_MODE_LOADING            3U

#define KEY_USER_GPIO_PORT              GPIOA
#define KEY_USER_PIN                    GPIO_PIN_0
#define KEY_USER_ACTIVE_STATE           GPIO_PIN_RESET
#define KEY_DEBOUNCE_MS                 40U

#define AF_MAX_PPI_COUNT                100U
#define AF_NB_FEATURE_COUNT             11U
#define AF_NB_MAX_EDGE_COUNT            9U
#define AF_NB_MAX_BIN_COUNT             10U
#define AF_NB_CLASS_NORMAL              0U
#define AF_NB_CLASS_AF                  1U
#define AF_MIN_PPI_COUNT                20U
#define AF_WINDOW_TARGET_MS             30000U
#define AF_RISK_FAST_STEP_MS            10000U
#define AF_RISK_SLOW_STEP_MS            30000U
#define AF_STABLE_RISK_THRESHOLD_PERCENT 20
#define AF_MAX_UP_JUMP_PERCENT          20
#define AF_LIVE_HR_TOLERANCE_BPM        18.0f
#define AF_TEST_PLAYBACK_DIVISOR        1U

#define STRESS_HRV_MAX_PPI_COUNT        80U
#define STRESS_HRV_FEATURE_COUNT        13U
#define STRESS_HRV_MIN_INTERVAL_COUNT   28U
#define STRESS_HRV_WINDOW_TARGET_MS     40000U
#define STRESS_HRV_FIRST_STEP_MS        10000U
#define STRESS_HRV_REFRESH_STEP_MS      30000U
#define STRESS_HRV_FIRST_UPDATE_INTERVAL_SAMPLES (STRESS_HRV_FIRST_STEP_MS / MAX30102_SAMPLE_INTERVAL_MS)
#define STRESS_HRV_REFRESH_UPDATE_INTERVAL_SAMPLES (STRESS_HRV_REFRESH_STEP_MS / MAX30102_SAMPLE_INTERVAL_MS)
#define STRESS_HRV_HIGH_HR_BPM         120
#define STRESS_HRV_INDEX_MIN            1
#define STRESS_HRV_INDEX_MAX            99

/* USER CODE END PD */

/* Private macro -------------------------------------------------------------*/
/* USER CODE BEGIN PM */

/* USER CODE END PM */

/* Private variables ---------------------------------------------------------*/

/* USER CODE BEGIN PV */
extern USBD_HandleTypeDef hUsbDeviceFS;

static uint8_t i2c1_found_addr[128];
static uint8_t i2c2_found_addr[128];
static uint8_t i2c3_found_addr[128];

volatile uint8_t i2c1_found_count = 0U;
volatile uint8_t i2c2_found_count = 0U;
volatile uint8_t i2c3_found_count = 0U;

volatile uint8_t oled_096_ready = 0U;
volatile uint8_t oled_091_ready = 0U;
volatile uint8_t max30102_ready = 0U;
volatile uint8_t max30102_int_pending = 0U;
volatile uint8_t startup_report_sent = 0U;
volatile uint8_t oled_096_display_ready = 0U;
volatile uint8_t oled_091_display_ready = 0U;
volatile uint8_t max30102_initialized = 0U;
volatile int16_t heart_rate_bpm = -1;
volatile int16_t spo2_percent = -1;
volatile uint8_t heart_flash_active = 0U;
volatile uint32_t max30102_irq_count = 0U;
volatile uint32_t max30102_samples_total = 0U;
volatile uint32_t max30102_last_red_raw = 0U;
volatile uint32_t max30102_last_ir_raw = 0U;
volatile uint16_t ppg_signal_amp = 0U;
volatile uint8_t finger_present = 0U;
volatile uint8_t ppg_signal_valid = 0U;
volatile uint8_t raw_stream_header_sent = 0U;
volatile int16_t hr_autocorr_bpm = -1;
volatile uint16_t ppg_finger_score_x100 = 0U;
volatile uint16_t ppg_ratio_x1000 = 0U;
volatile int16_t af_risk_percent = -1;
volatile int16_t af_test_risk_percent = -1;
volatile int16_t stress_hrv_index = -1;

static PPG_State_t ppg_state;
static PPG_AutocorrJob_t ppg_autocorr_job;
static PPG_RobustPeakState_t ppg_robust_peak;
static DiagnosticStats_t diag_stats;
static uint8_t oled64_buffer[OLED_WIDTH * (OLED_096_HEIGHT / 8U)];
static uint8_t oled32_buffer[OLED_WIDTH * (OLED_091_HEIGHT / 8U)];
static OLED091_WaveformState_t oled091_waveform;
static uint8_t diag_stream_header_sent = 0U;
static uint8_t ppg_proc_header_sent = 0U;
static uint32_t diag_last_loop_tick_ms = 0U;
static uint32_t heart_flash_until_ms = 0U;
static uint32_t next_max30102_poll_ms = 0U;
static uint32_t last_oled_vitals_update_ms = 0U;
static volatile uint32_t last_max30102_irq_ms = 0U;
static uint32_t last_vitals_log_ms = 0U;
static AppDisplayMode_t app_display_mode = APP_DISPLAY_VITALS;
static uint8_t key_last_raw_pressed = 0U;
static uint8_t key_stable_pressed = 0U;
static uint32_t key_last_change_ms = 0U;
static uint16_t af_live_ppi_ms[AF_MAX_PPI_COUNT];
static uint16_t af_live_ppi_count = 0U;
static uint16_t af_live_ppi_index = 0U;
static uint32_t af_live_last_peak_ms = 0U;
static uint32_t af_live_last_risk_sample = 0U;
static uint16_t af_live_last_interval_ms = 0U;
static uint8_t af_live_last_interval_accepted = 0U;
static uint32_t af_live_accepted_ppi_count = 0U;
static uint32_t af_live_rejected_ppi_count = 0U;
static uint32_t af_live_quality_accept_snapshot = 0U;
static uint32_t af_live_quality_reject_snapshot = 0U;
static uint16_t af_test_window_ppi_ms[AF_MAX_PPI_COUNT];
static uint16_t af_test_window_count = 0U;
static uint16_t af_test_source_index = 0U;
static uint32_t af_test_next_interval_ms = 0U;
static uint8_t af_test_playback_active = 0U;
static uint16_t stress_live_ppi_ms[STRESS_HRV_MAX_PPI_COUNT];
static uint16_t stress_live_ppi_count = 0U;
static uint16_t stress_live_ppi_index = 0U;
static uint32_t stress_live_last_peak_ms = 0U;
static uint32_t stress_live_last_index_sample = 0U;
static uint16_t stress_live_last_interval_ms = 0U;
static uint8_t stress_live_last_interval_accepted = 0U;
static uint32_t stress_live_accepted_ppi_count = 0U;
static uint32_t stress_live_rejected_ppi_count = 0U;
static uint32_t stress_live_quality_accept_snapshot = 0U;
static uint32_t stress_live_quality_reject_snapshot = 0U;

static const uint16_t af_test_ppi_ms[] = {
  /* AFDB 06995, 9030s..9060s, AF rhythm, 30s RR/PPI window, PC risk ~=85%. */
  540U, 800U, 784U, 556U, 800U, 760U, 780U, 796U, 792U, 752U, 756U, 776U,
  784U, 788U, 788U, 756U, 772U, 696U, 868U, 764U, 536U, 836U, 804U, 816U,
  804U, 784U, 772U, 800U, 808U, 404U, 1104U, 644U, 552U, 840U, 692U, 840U,
  764U, 760U, 776U, 800U
};

static const float af_nb_class_log_prior[2] = {
  -0.375503344f, -1.16137135f
};

static const uint8_t af_nb_edge_count[AF_NB_FEATURE_COUNT] = {
  6U, 8U, 9U, 9U, 9U, 9U, 9U, 9U, 8U, 8U, 8U
};

static const float af_nb_bin_edges[AF_NB_FEATURE_COUNT][AF_NB_MAX_EDGE_COUNT] = {
  {20.0f, 30.0f, 40.0f, 50.0f, 70.0f, 100.0f, 0.0f, 0.0f, 0.0f},
  {450.0f, 550.0f, 650.0f, 750.0f, 850.0f, 1000.0f, 1200.0f, 1600.0f, 0.0f},
  {20.0f, 40.0f, 60.0f, 90.0f, 130.0f, 180.0f, 260.0f, 400.0f, 700.0f},
  {10.0f, 20.0f, 35.0f, 55.0f, 80.0f, 120.0f, 180.0f, 260.0f, 420.0f},
  {0.02f, 0.04f, 0.06f, 0.09f, 0.13f, 0.18f, 0.25f, 0.35f, 0.55f},
  {5.0f, 10.0f, 20.0f, 35.0f, 55.0f, 80.0f, 120.0f, 180.0f, 280.0f},
  {20.0f, 40.0f, 60.0f, 90.0f, 130.0f, 180.0f, 260.0f, 400.0f, 700.0f},
  {40.0f, 80.0f, 120.0f, 180.0f, 260.0f, 400.0f, 700.0f, 1000.0f, 1600.0f},
  {1.0f, 5.0f, 10.0f, 20.0f, 35.0f, 50.0f, 70.0f, 90.0f, 0.0f},
  {1.0f, 5.0f, 10.0f, 20.0f, 35.0f, 50.0f, 70.0f, 90.0f, 0.0f},
  {1.0f, 5.0f, 10.0f, 20.0f, 35.0f, 50.0f, 70.0f, 90.0f, 0.0f}
};

static const float af_nb_log_prob_normal[AF_NB_FEATURE_COUNT][AF_NB_MAX_BIN_COUNT] = {
  {-13.2413901f, -2.1408335f, -0.635652452f, -1.26213649f, -2.68224956f, -6.57698106f, -13.2413901f, 0.0f, 0.0f, 0.0f},
  {-5.81722835f, -3.76930457f, -2.14245448f, -1.57318385f, -1.48981354f, -1.22584005f, -2.0960163f, -4.91536095f, -13.2413936f, 0.0f},
  {-1.64410122f, -0.95439737f, -1.80431481f, -2.09930177f, -2.9290153f, -3.54992603f, -3.77200395f, -4.10847619f, -4.38146394f, -7.56464161f},
  {-1.94431519f, -1.04839281f, -1.37088641f, -2.20182318f, -3.06509834f, -3.14642011f, -3.43541044f, -4.16173336f, -5.40779519f, -13.2413954f},
  {-0.989475021f, -1.04338518f, -2.14344008f, -2.87000017f, -3.40881327f, -3.43844668f, -3.59596641f, -4.74685691f, -7.25745913f, -12.1427831f},
  {-4.37636622f, -0.950255777f, -1.10365514f, -1.90552937f, -3.11440437f, -4.35443338f, -4.39030472f, -4.03838181f, -4.13530506f, -4.06803027f},
  {-1.12528577f, -0.852255653f, -2.46404458f, -3.16972748f, -4.07218958f, -4.47843649f, -3.86266339f, -3.30119028f, -3.44404645f, -5.5979125f},
  {-0.668193162f, -1.4519828f, -2.9793946f, -3.4158694f, -3.55724616f, -2.96438071f, -2.6348351f, -4.06502556f, -7.44533766f, -13.2413954f},
  {-0.755301953f, -2.19115464f, -2.22115922f, -2.22532514f, -2.55238485f, -3.23404169f, -3.22364034f, -3.32835187f, -4.72480062f, 0.0f},
  {-0.406104515f, -2.6780055f, -2.64884223f, -2.78155275f, -3.15446016f, -3.6377957f, -3.34629114f, -3.7025412f, -5.6444992f, 0.0f},
  {-0.294403212f, -3.16689829f, -2.88511219f, -3.06330925f, -3.39717849f, -3.65611609f, -3.37914483f, -4.18560402f, -6.36616155f, 0.0f}
};

static const float af_nb_log_prob_af[AF_NB_FEATURE_COUNT][AF_NB_MAX_BIN_COUNT] = {
  {-12.4555348f, -3.33752904f, -1.23436637f, -0.91143049f, -1.38366017f, -3.8708699f, -12.4555348f, 0.0f, 0.0f, 0.0f},
  {-3.31102157f, -2.11667521f, -1.515797f, -1.30434769f, -1.65686538f, -2.12672195f, -3.30008089f, -5.24768272f, -12.4555426f, 0.0f},
  {-5.17968189f, -4.41574415f, -4.25060133f, -2.91116541f, -1.78875623f, -1.06417868f, -1.18299975f, -2.46606475f, -4.63390337f, -7.71934804f},
  {-5.52407469f, -4.7422086f, -4.25690704f, -2.74255381f, -1.41605453f, -0.732206143f, -1.81434479f, -4.05803814f, -5.31035036f, -10.2583219f},
  {-4.61641484f, -4.66319757f, -4.4316665f, -2.62055194f, -1.11897642f, -0.850744007f, -2.02916641f, -4.54715933f, -8.25085387f, -12.4555465f},
  {-8.26589175f, -5.27623852f, -4.93152508f, -4.16024763f, -2.99873674f, -2.11425572f, -1.08773929f, -1.01158533f, -2.44284585f, -4.28989857f},
  {-5.72452839f, -4.81542332f, -5.41064137f, -4.51105433f, -3.02808064f, -1.80012412f, -0.873262889f, -1.2110758f, -3.19422788f, -5.80010614f},
  {-5.32624894f, -5.46990467f, -5.87907692f, -3.64972168f, -1.95892548f, -0.726059087f, -1.15473809f, -3.93875338f, -5.91740667f, -12.4555465f},
  {-5.42822808f, -6.3199777f, -5.91307064f, -5.31826416f, -4.54019443f, -3.46647253f, -1.33876082f, -0.45487182f, -3.0447952f, 0.0f},
  {-4.91492107f, -6.68410147f, -5.68475317f, -4.61287112f, -3.15399134f, -2.0191353f, -0.617574247f, -1.36125867f, -4.95446047f, 0.0f},
  {-4.80347185f, -5.93051294f, -4.74892968f, -3.31048769f, -1.92339305f, -1.11907988f, -0.87808486f, -2.91921332f, -6.35522364f, 0.0f}
};

static const float stress_hrv_scaler_mean[STRESS_HRV_FEATURE_COUNT] = {
  52.0606722f, 78.6382769f, 784.096698f, 196.588208f, 246.779637f, 249.251619f,
  80.1850478f, 61.8870328f, 0.255469766f, 121.535356f, 285.219337f,
  504.326372f, 0.140743524f
};

static const float stress_hrv_scaler_scale[STRESS_HRV_FEATURE_COUNT] = {
  9.02599589f, 13.3570937f, 127.352086f, 78.825009f, 106.343028f,
  107.543794f, 15.7302021f, 21.8541042f, 0.103128021f, 83.8258933f,
  153.028078f, 219.654648f, 0.108975149f
};

static const float stress_hrv_coef[STRESS_HRV_FEATURE_COUNT] = {
  -0.463413349f, 3.29944449f, -0.574488646f, 3.62520799f, -0.12256324f,
  0.00743854681f, 0.953966047f, -0.858624879f, -1.43593046f, 0.270093131f,
  0.704202075f, 0.5918776f, -1.51190536f
};

static const float stress_hrv_intercept = -1.28988243f;
static const float stress_hrv_prob_nonstress_median = 0.0972647709f;
static const float stress_hrv_prob_stress_median = 0.867810518f;
static const float stress_hrv_index_nonstress_target = 45.0f;
static const float stress_hrv_index_stress_target = 82.0f;
static const float stress_hrv_display_index_bias = 5.0f;

/* USER CODE END PV */

/* Private function prototypes -----------------------------------------------*/
void SystemClock_Config(void);
/* USER CODE BEGIN PFP */
static void USB_CDC_WaitConfigured(uint32_t timeout_ms);
static void App_KeyInit(void);
static void App_KeyService(uint32_t now_ms);
static void App_AdvanceDisplayMode(void);
static void App_StartAfTestPlayback(void);
static void App_UpdateAfTestRisk(void);
static void AF_TestPlaybackService(uint32_t now_ms);
static void AF_TestAppendPpi(uint16_t ppi_ms);
static void AF_LiveReset(void);
static void AF_LiveRecordPeak(uint32_t peak_time_ms);
static void AF_LiveUpdateRisk(void);
static uint32_t AF_LiveUpdateIntervalSamples(void);
static uint16_t AF_LiveCopyPpi(uint16_t *out_ppi_ms, uint16_t max_count);
static uint32_t AF_PpiDurationMs(const uint16_t *ppi_ms, uint16_t count);
static int16_t AF_RiskPercentFromPpi(const uint16_t *ppi_ms, uint16_t count);
static uint8_t AF_ComputeFeaturesFromPpi(const uint16_t *ppi_ms, uint16_t count, float *features);
static float AF_PredictRiskPercent(const float *features);
static uint8_t AF_BinIndex(uint8_t feature_index, float value);
static void AF_SortFloat(float *values, uint16_t count);
static float AF_PercentileSorted(const float *sorted_values, uint16_t count, float percentile);
static void Stress_LiveReset(void);
static void Stress_LiveRecordPeak(uint32_t peak_time_ms);
static void Stress_LiveUpdateIndex(void);
static uint16_t Stress_LiveCopyPpi(uint16_t *out_ppi_ms, uint16_t max_count);
static uint8_t Stress_ComputeFeaturesFromPpi(const uint16_t *ppi_ms, uint16_t count, float *features);
static float Stress_PredictProbability(const float *features);
static int16_t Stress_IndexFromProbability(float probability);
static const char *Stress_LevelText(int16_t index);
static void CDC_Printf(const char *fmt, ...);
static void Semihosting_WriteString(const char *str);
static void I2C_ScanBus(const char *name, I2C_HandleTypeDef *hi2c,
                        uint8_t *found_addr, volatile uint8_t *found_count);
static uint8_t I2C_IsReady7Bit(I2C_HandleTypeDef *hi2c, uint8_t addr7);
static void I2C_RunStartupScanner(void);
static void I2C_PrintStartupReport(void);
static void Diagnostics_RecordLoopTick(uint32_t now_ms);
static void Diagnostics_ResetInterval(void);
static void Diagnostics_UpdateMax(uint32_t *slot, uint32_t value);
static void Diagnostics_WriteHeaderIfNeeded(void);
static HAL_StatusTypeDef OLED_WriteCommand(I2C_HandleTypeDef *hi2c, uint8_t addr7, uint8_t cmd);
static HAL_StatusTypeDef OLED_WriteData(I2C_HandleTypeDef *hi2c, uint8_t addr7, const uint8_t *data, uint16_t len);
static HAL_StatusTypeDef OLED_SetPageColumn(I2C_HandleTypeDef *hi2c, uint8_t addr7, uint8_t page, uint8_t column);
static HAL_StatusTypeDef OLED_Init(I2C_HandleTypeDef *hi2c, uint8_t addr7, uint8_t height);
static void OLED_Clear(I2C_HandleTypeDef *hi2c, uint8_t addr7, uint8_t height);
static const uint8_t *OLED_Font5x7(char ch);
static void OLED_WriteText2x(I2C_HandleTypeDef *hi2c, uint8_t addr7, uint8_t x, uint8_t page, const char *text);
static void OLED_ShowStartupText(void);
static void OLED64_ClearBuffer(void);
static void OLED64_Flush(void);
static void OLED64_DrawPixel(int16_t x, int16_t y, uint8_t on);
static void OLED64_DrawLine(int16_t x0, int16_t y0, int16_t x1, int16_t y1, uint8_t on);
static void OLED64_FillRect(uint8_t x, uint8_t y, uint8_t w, uint8_t h, uint8_t on);
static void OLED64_DrawChar5x7(uint8_t x, uint8_t y, char ch, uint8_t scale);
static void OLED64_DrawText5x7(uint8_t x, uint8_t y, const char *text, uint8_t scale);
static void OLED64_DrawHeartIcon(uint8_t x, uint8_t y, uint8_t filled);
static void OLED64_DrawHeartRateLabel(uint8_t x, uint8_t y);
static void OLED64_DrawSpo2Label(uint8_t x, uint8_t y);
static void OLED64_DrawCenteredText5x7(uint8_t y, const char *text, uint8_t scale);
static void OLED64_RenderAfRisk(uint8_t test_mode);
static void OLED64_RenderStress(void);
static void OLED64_RenderVitals(void);
static void OLED091_WaveformReset(void);
static void OLED091_WaveformService(uint32_t now_ms);
static void OLED091_WaveformUpdate(uint32_t sample_count, uint8_t beat_peak_detected);
static void OLED091_WaveformShiftInsert(uint8_t y);
static uint8_t OLED091_WaveformPulseY(uint32_t sample_count);
static uint8_t OLED091_WaveformPeakHeightPx(float peak_value);
static uint8_t OLED091_DisplayMode(void);
static void OLED091_WaveformRenderBuffer(void);
static void OLED091_RenderStatus(uint8_t mode);
static void OLED091_ClearBuffer(void);
static void OLED091_Flush(void);
static void OLED091_DrawPixel(int16_t x, int16_t y, uint8_t on);
static void OLED091_DrawLine(int16_t x0, int16_t y0, int16_t x1, int16_t y1, uint8_t on);
static void OLED091_FillRect(uint8_t x, uint8_t y, uint8_t w, uint8_t h, uint8_t on);
static void OLED091_DrawChar5x7(uint8_t x, uint8_t y, char ch, uint8_t scale);
static void OLED091_DrawText5x7(uint8_t x, uint8_t y, const char *text, uint8_t scale);
static HAL_StatusTypeDef MAX30102_WriteReg(uint8_t reg, uint8_t value);
static HAL_StatusTypeDef MAX30102_ReadReg(uint8_t reg, uint8_t *value);
static void MAX30102_ClearInterrupts(void);
static HAL_StatusTypeDef MAX30102_InitSensor(void);
static uint8_t MAX30102_FIFOCount(void);
static uint8_t MAX30102_ReadSamples(MAX30102_Sample_t *samples, uint8_t max_samples);
static void MAX30102_Service(void);
static void RawStream_WriteHeaderIfNeeded(void);
static void RawStream_WriteSample(uint32_t sample_time_ms, uint32_t red_raw, uint32_t ir_raw);
static void PPG_Reset(void);
static void PPG_ClearMeasurementState(void);
static void PPG_ClearHrState(void);
static void PPG_ClearVitalsState(void);
static void PPG_ProcessSample(uint32_t red_raw, uint32_t ir_raw);
static void PPG_AppendWindow(float red_filt, float ir_filt);
static float PPG_WindowValue(const float *buffer, uint16_t len, uint16_t pos);
static float PPG_RmsWindow(const float *buffer, uint16_t len);
static uint8_t PPG_DetectPeak(uint32_t sample_time_ms);
static void PPG_RobustPeakReset(void);
static uint8_t PPG_DetectRobustPeak(uint32_t sample_time_ms, uint32_t *peak_time_ms);
static uint8_t PPG_RobustFlushPendingPeak(uint32_t sample_time_ms, uint32_t *peak_time_ms);
static void PPG_RobustStorePendingPeak(uint32_t peak_time_ms, float peak_value);
static void PPG_RobustAppendPeak(uint32_t peak_time_ms, float peak_value);
static float PPG_RobustReferencePpiMs(void);
static uint32_t PPG_RobustDynamicMinPeakMs(void);
static float PPG_RobustPeakThreshold(uint32_t peak_time_ms);
static uint8_t PPG_HrvPpiIsUsable(uint32_t interval_ms);
static float PPG_HrvCurrentBpm(void);
static void PPG_HrvPpiRatioLimits(float bpm, float *min_ratio, float *max_ratio);
static uint8_t PPG_HrvWindowQualityOk(uint32_t accepted_count, uint32_t rejected_count);
static float PPG_EstimateHrAutocorr(void);
static void PPG_AutocorrStart(uint32_t sample_count);
static void PPG_AutocorrService(void);
static void PPG_AutocorrCancel(void);
static void PPG_HandleAutocorrResult(float hr, uint32_t sample_count);
static void PPG_AppendHrHistory(float hr_bpm);
static float PPG_MedianFloat(float *values, uint8_t count);
static int16_t PPG_RoundToInt16(float value);
static int32_t PPG_RoundToInt32(float value);
static uint8_t PPG_ShouldAcceptHrDisplay(int16_t next_hr_bpm);
static void PPG_ClearHrConfirmState(void);
static uint8_t PPG_UpdateHrConfirmation(float hr_bpm_value, uint32_t sample_count, float *confirmed_hr);
static void Vitals_UpdateTimeouts(void);
static void Vitals_LogStatus(void);
static float ClampFloat(float value, float low, float high);
static uint8_t ClampU8(int16_t value, uint8_t low, uint8_t high);

/* USER CODE END PFP */

/* Private user code ---------------------------------------------------------*/
/* USER CODE BEGIN 0 */

/* USER CODE END 0 */

/**
  * @brief  The application entry point.
  * @retval int
  */
int main(void)
{

  /* USER CODE BEGIN 1 */

  /* USER CODE END 1 */

  /* MCU Configuration--------------------------------------------------------*/

  /* Reset of all peripherals, Initializes the Flash interface and the Systick. */
  HAL_Init();

  /* USER CODE BEGIN Init */

  /* USER CODE END Init */

  /* Configure the system clock */
  SystemClock_Config();

  /* USER CODE BEGIN SysInit */

  /* USER CODE END SysInit */

  /* Initialize all configured peripherals */
  MX_GPIO_Init();
  MX_I2C1_Init();
  MX_I2C2_Init();
  MX_I2C3_Init();
  MX_USB_DEVICE_Init();
  /* USER CODE BEGIN 2 */
  App_KeyInit();
  USB_CDC_WaitConfigured(USB_ENUM_WAIT_MS);
  I2C_RunStartupScanner();
  OLED_ShowStartupText();
  OLED091_WaveformReset();
  PPG_Reset();
  max30102_initialized = (MAX30102_InitSensor() == HAL_OK) ? 1U : 0U;
  CDC_Printf("MAX30102 init: %s\r\n", max30102_initialized ? "OK" : "FAIL");
  OLED64_RenderVitals();
  if (CDC_IsPortOpen_FS() != 0U)
  {
    I2C_PrintStartupReport();
    startup_report_sent = 1U;
  }

  /* USER CODE END 2 */

  /* Infinite loop */
  /* USER CODE BEGIN WHILE */
  while (1)
  {
    /* USER CODE END WHILE */

    /* USER CODE BEGIN 3 */
    uint32_t now_ms = HAL_GetTick();
    Diagnostics_RecordLoopTick(now_ms);
    App_KeyService(now_ms);
    AF_TestPlaybackService(now_ms);

    if ((startup_report_sent == 0U) && (CDC_IsPortOpen_FS() != 0U))
    {
      I2C_PrintStartupReport();
      startup_report_sent = 1U;
    }

    if (max30102_int_pending != 0U)
    {
      max30102_int_pending = 0U;
      MAX30102_Service();
    }

    if ((max30102_initialized != 0U) &&
        ((int32_t)(now_ms - next_max30102_poll_ms) >= 0))
    {
      next_max30102_poll_ms = now_ms + MAX30102_FALLBACK_POLL_MS;
      if ((last_max30102_irq_ms == 0U) || ((now_ms - last_max30102_irq_ms) > MAX30102_FALLBACK_POLL_MS))
      {
        MAX30102_Service();
      }
    }

    Vitals_UpdateTimeouts();
    OLED091_WaveformService(now_ms);
    if ((oled_096_display_ready != 0U) &&
        ((int32_t)(now_ms - last_oled_vitals_update_ms) >= 0))
    {
      last_oled_vitals_update_ms = now_ms + OLED_VITALS_UPDATE_MS;
      OLED64_RenderVitals();
    }
    else if ((oled_096_display_ready != 0U) &&
             (heart_flash_active != 0U) &&
             ((int32_t)(now_ms - heart_flash_until_ms) >= 0))
    {
      OLED64_RenderVitals();
    }

    if ((int32_t)(now_ms - last_vitals_log_ms) >= 0)
    {
      last_vitals_log_ms = now_ms + OLED_VITALS_UPDATE_MS;
      Vitals_LogStatus();
    }

    if (max30102_int_pending == 0U)
    {
      PPG_AutocorrService();
    }
  }
  /* USER CODE END 3 */
}

/**
  * @brief System Clock Configuration
  * @retval None
  */
void SystemClock_Config(void)
{
  RCC_OscInitTypeDef RCC_OscInitStruct = {0};
  RCC_ClkInitTypeDef RCC_ClkInitStruct = {0};

  /** Configure the main internal regulator output voltage
  */
  __HAL_RCC_PWR_CLK_ENABLE();
  __HAL_PWR_VOLTAGESCALING_CONFIG(PWR_REGULATOR_VOLTAGE_SCALE1);

  /** Initializes the RCC Oscillators according to the specified parameters
  * in the RCC_OscInitTypeDef structure.
  */
  RCC_OscInitStruct.OscillatorType = RCC_OSCILLATORTYPE_HSE;
  RCC_OscInitStruct.HSEState = RCC_HSE_ON;
  RCC_OscInitStruct.PLL.PLLState = RCC_PLL_ON;
  RCC_OscInitStruct.PLL.PLLSource = RCC_PLLSOURCE_HSE;
  RCC_OscInitStruct.PLL.PLLM = 25;
  RCC_OscInitStruct.PLL.PLLN = 192;
  RCC_OscInitStruct.PLL.PLLP = RCC_PLLP_DIV2;
  RCC_OscInitStruct.PLL.PLLQ = 4;
  if (HAL_RCC_OscConfig(&RCC_OscInitStruct) != HAL_OK)
  {
    Error_Handler();
  }

  /** Initializes the CPU, AHB and APB buses clocks
  */
  RCC_ClkInitStruct.ClockType = RCC_CLOCKTYPE_HCLK|RCC_CLOCKTYPE_SYSCLK
                              |RCC_CLOCKTYPE_PCLK1|RCC_CLOCKTYPE_PCLK2;
  RCC_ClkInitStruct.SYSCLKSource = RCC_SYSCLKSOURCE_PLLCLK;
  RCC_ClkInitStruct.AHBCLKDivider = RCC_SYSCLK_DIV1;
  RCC_ClkInitStruct.APB1CLKDivider = RCC_HCLK_DIV2;
  RCC_ClkInitStruct.APB2CLKDivider = RCC_HCLK_DIV1;

  if (HAL_RCC_ClockConfig(&RCC_ClkInitStruct, FLASH_LATENCY_3) != HAL_OK)
  {
    Error_Handler();
  }
}

/* USER CODE BEGIN 4 */
static void USB_CDC_WaitConfigured(uint32_t timeout_ms)
{
  uint32_t start_tick = HAL_GetTick();

  while (hUsbDeviceFS.dev_state != USBD_STATE_CONFIGURED)
  {
    if ((HAL_GetTick() - start_tick) >= timeout_ms)
    {
      break;
    }
    HAL_Delay(10U);
  }
}

static void App_KeyInit(void)
{
  GPIO_InitTypeDef GPIO_InitStruct = {0};
  uint8_t pressed;

  __HAL_RCC_GPIOA_CLK_ENABLE();

  GPIO_InitStruct.Pin = KEY_USER_PIN;
  GPIO_InitStruct.Mode = GPIO_MODE_INPUT;
  GPIO_InitStruct.Pull = GPIO_PULLUP;
  GPIO_InitStruct.Speed = GPIO_SPEED_FREQ_LOW;
  HAL_GPIO_Init(KEY_USER_GPIO_PORT, &GPIO_InitStruct);

  pressed = (HAL_GPIO_ReadPin(KEY_USER_GPIO_PORT, KEY_USER_PIN) == KEY_USER_ACTIVE_STATE) ? 1U : 0U;
  key_last_raw_pressed = pressed;
  key_stable_pressed = pressed;
  key_last_change_ms = HAL_GetTick();
}

static void App_KeyService(uint32_t now_ms)
{
  uint8_t raw_pressed = (HAL_GPIO_ReadPin(KEY_USER_GPIO_PORT, KEY_USER_PIN) == KEY_USER_ACTIVE_STATE) ? 1U : 0U;

  if (raw_pressed != key_last_raw_pressed)
  {
    key_last_raw_pressed = raw_pressed;
    key_last_change_ms = now_ms;
    return;
  }

  if ((now_ms - key_last_change_ms) < KEY_DEBOUNCE_MS)
  {
    return;
  }

  if (raw_pressed == key_stable_pressed)
  {
    return;
  }

  key_stable_pressed = raw_pressed;
  if (key_stable_pressed != 0U)
  {
    App_AdvanceDisplayMode();
  }
}

static void App_AdvanceDisplayMode(void)
{
  if (app_display_mode == APP_DISPLAY_VITALS)
  {
    app_display_mode = APP_DISPLAY_AF_LIVE;
    AF_LiveUpdateRisk();
  }
  else if (app_display_mode == APP_DISPLAY_AF_LIVE)
  {
    app_display_mode = APP_DISPLAY_STRESS;
    Stress_LiveUpdateIndex();
  }
  else if (app_display_mode == APP_DISPLAY_STRESS)
  {
    app_display_mode = APP_DISPLAY_AF_TEST;
    App_StartAfTestPlayback();
  }
  else
  {
    app_display_mode = APP_DISPLAY_VITALS;
    af_test_playback_active = 0U;
  }

  last_oled_vitals_update_ms = 0U;
}

static void App_StartAfTestPlayback(void)
{
  memset(af_test_window_ppi_ms, 0, sizeof(af_test_window_ppi_ms));
  af_test_window_count = 0U;
  af_test_source_index = 0U;
  af_test_playback_active = 1U;
  af_test_risk_percent = -1;

  if (sizeof(af_test_ppi_ms) > 0U)
  {
    af_test_next_interval_ms = HAL_GetTick() +
        ((uint32_t)af_test_ppi_ms[0] / AF_TEST_PLAYBACK_DIVISOR);
  }
  else
  {
    af_test_playback_active = 0U;
    af_test_next_interval_ms = 0U;
  }
}

static void App_UpdateAfTestRisk(void)
{
  if ((af_test_window_count < AF_MIN_PPI_COUNT) ||
      (AF_PpiDurationMs(af_test_window_ppi_ms, af_test_window_count) < AF_WINDOW_TARGET_MS))
  {
    af_test_risk_percent = -1;
  }
  else
  {
    af_test_risk_percent = AF_RiskPercentFromPpi(af_test_window_ppi_ms, af_test_window_count);
  }
}

static void AF_TestPlaybackService(uint32_t now_ms)
{
  uint16_t source_count = (uint16_t)(sizeof(af_test_ppi_ms) / sizeof(af_test_ppi_ms[0]));

  if ((app_display_mode != APP_DISPLAY_AF_TEST) ||
      (af_test_playback_active == 0U) ||
      (source_count == 0U))
  {
    return;
  }

  while ((af_test_playback_active != 0U) &&
         ((int32_t)(now_ms - af_test_next_interval_ms) >= 0))
  {
    AF_TestAppendPpi(af_test_ppi_ms[af_test_source_index]);
    af_test_source_index++;
    App_UpdateAfTestRisk();
    if ((af_test_risk_percent >= 0) || (af_test_source_index == 1U))
    {
      last_oled_vitals_update_ms = 0U;
    }

    if (af_test_source_index >= source_count)
    {
      af_test_playback_active = 0U;
      break;
    }

    af_test_next_interval_ms +=
        ((uint32_t)af_test_ppi_ms[af_test_source_index] / AF_TEST_PLAYBACK_DIVISOR);
  }
}

static void AF_TestAppendPpi(uint16_t ppi_ms)
{
  if (af_test_window_count >= AF_MAX_PPI_COUNT)
  {
    uint16_t i;

    for (i = 1U; i < AF_MAX_PPI_COUNT; i++)
    {
      af_test_window_ppi_ms[i - 1U] = af_test_window_ppi_ms[i];
    }
    af_test_window_ppi_ms[AF_MAX_PPI_COUNT - 1U] = ppi_ms;
  }
  else
  {
    af_test_window_ppi_ms[af_test_window_count] = ppi_ms;
    af_test_window_count++;
  }
}

static void AF_LiveReset(void)
{
  memset(af_live_ppi_ms, 0, sizeof(af_live_ppi_ms));
  af_live_ppi_count = 0U;
  af_live_ppi_index = 0U;
  af_live_last_peak_ms = 0U;
  af_live_last_risk_sample = 0U;
  af_live_last_interval_ms = 0U;
  af_live_last_interval_accepted = 0U;
  af_live_accepted_ppi_count = 0U;
  af_live_rejected_ppi_count = 0U;
  af_live_quality_accept_snapshot = 0U;
  af_live_quality_reject_snapshot = 0U;
  af_risk_percent = -1;
}

static void AF_LiveRecordPeak(uint32_t peak_time_ms)
{
  af_live_last_interval_ms = 0U;
  af_live_last_interval_accepted = 0U;

  if (af_live_last_peak_ms != 0U)
  {
    uint32_t interval_ms = peak_time_ms - af_live_last_peak_ms;
    af_live_last_interval_ms = (interval_ms > 65535U) ? 65535U : (uint16_t)interval_ms;

    if (PPG_HrvPpiIsUsable(interval_ms) != 0U)
    {
      af_live_ppi_ms[af_live_ppi_index] = (uint16_t)interval_ms;
      af_live_last_interval_accepted = 1U;
      af_live_accepted_ppi_count++;
      af_live_ppi_index++;
      if (af_live_ppi_index >= AF_MAX_PPI_COUNT)
      {
        af_live_ppi_index = 0U;
      }
      if (af_live_ppi_count < AF_MAX_PPI_COUNT)
      {
        af_live_ppi_count++;
      }
    }
    else
    {
      af_live_rejected_ppi_count++;
    }
  }

  af_live_last_peak_ms = peak_time_ms;
}

static void AF_LiveUpdateRisk(void)
{
  uint16_t ppi_ms[AF_MAX_PPI_COUNT];
  uint16_t ppi_count;
  uint32_t ppi_duration_ms;
  uint32_t update_interval_samples;
  uint32_t accepted_since_update;
  uint32_t rejected_since_update;
  int16_t next_risk;

  if ((finger_present == 0U) ||
      (ppg_state.raw_finger_present == 0U) ||
      (ppg_state.finger_score < PPG_MIN_SPO2_SCORE))
  {
    if (af_risk_percent != -1)
    {
      AF_LiveReset();
      if (app_display_mode == APP_DISPLAY_AF_LIVE)
      {
        last_oled_vitals_update_ms = 0U;
      }
    }
    else if ((af_live_ppi_count != 0U) || (af_live_last_peak_ms != 0U))
    {
      AF_LiveReset();
    }
    return;
  }

  if ((ppg_signal_valid == 0U) ||
      (heart_rate_bpm <= 0) ||
      (spo2_percent <= 0))
  {
    return;
  }

  update_interval_samples = AF_LiveUpdateIntervalSamples();
  if ((af_live_last_risk_sample != 0U) &&
      ((max30102_samples_total - af_live_last_risk_sample) < update_interval_samples))
  {
    return;
  }
  af_live_last_risk_sample = max30102_samples_total;

  accepted_since_update = af_live_accepted_ppi_count - af_live_quality_accept_snapshot;
  rejected_since_update = af_live_rejected_ppi_count - af_live_quality_reject_snapshot;
  af_live_quality_accept_snapshot = af_live_accepted_ppi_count;
  af_live_quality_reject_snapshot = af_live_rejected_ppi_count;
  if (PPG_HrvWindowQualityOk(accepted_since_update, rejected_since_update) == 0U)
  {
    return;
  }

  ppi_count = AF_LiveCopyPpi(ppi_ms, AF_MAX_PPI_COUNT);
  ppi_duration_ms = AF_PpiDurationMs(ppi_ms, ppi_count);
  if ((ppi_count < AF_MIN_PPI_COUNT) || (ppi_duration_ms < AF_WINDOW_TARGET_MS))
  {
    if (af_risk_percent >= 0)
    {
      return;
    }
    next_risk = -1;
  }
  else
  {
    float mean_ppi_ms = (float)ppi_duration_ms / (float)ppi_count;
    float ppi_hr_bpm = (mean_ppi_ms > 0.0f) ? (60000.0f / mean_ppi_ms) : 0.0f;
    float hr_diff = fabsf(ppi_hr_bpm - (float)heart_rate_bpm);

    if (hr_diff > AF_LIVE_HR_TOLERANCE_BPM)
    {
      next_risk = -1;
    }
    else
    {
      next_risk = AF_RiskPercentFromPpi(ppi_ms, ppi_count);
    }
  }

  if (next_risk < 0)
  {
    if (af_risk_percent >= 0)
    {
      return;
    }
  }
  else if ((af_risk_percent >= 0) &&
           (next_risk > (int16_t)(af_risk_percent + AF_MAX_UP_JUMP_PERCENT)))
  {
    return;
  }

  if (next_risk != af_risk_percent)
  {
    af_risk_percent = next_risk;
    if (app_display_mode == APP_DISPLAY_AF_LIVE)
    {
      last_oled_vitals_update_ms = 0U;
    }
  }
}

static uint32_t AF_LiveUpdateIntervalSamples(void)
{
  if ((af_risk_percent >= 0) &&
      (af_risk_percent < AF_STABLE_RISK_THRESHOLD_PERCENT))
  {
    return AF_RISK_SLOW_STEP_MS / MAX30102_SAMPLE_INTERVAL_MS;
  }

  return AF_RISK_FAST_STEP_MS / MAX30102_SAMPLE_INTERVAL_MS;
}

static uint16_t AF_LiveCopyPpi(uint16_t *out_ppi_ms, uint16_t max_count)
{
  uint16_t chronological[AF_MAX_PPI_COUNT];
  uint16_t count;
  uint16_t start;
  uint16_t selected_start;
  uint32_t selected_duration_ms = 0U;
  uint16_t i;

  if ((out_ppi_ms == NULL) || (max_count == 0U))
  {
    return 0U;
  }

  count = af_live_ppi_count;
  if (count > max_count)
  {
    count = max_count;
  }

  start = (af_live_ppi_count < AF_MAX_PPI_COUNT) ? 0U : af_live_ppi_index;
  for (i = 0U; i < count; i++)
  {
    uint16_t index = (uint16_t)(start + i);

    if (index >= AF_MAX_PPI_COUNT)
    {
      index = (uint16_t)(index - AF_MAX_PPI_COUNT);
    }
    chronological[i] = af_live_ppi_ms[index];
  }

  selected_start = count;
  while ((selected_start > 0U) && (selected_duration_ms < AF_WINDOW_TARGET_MS))
  {
    selected_start--;
    selected_duration_ms += chronological[selected_start];
  }

  count = (uint16_t)(count - selected_start);
  if (count > max_count)
  {
    count = max_count;
  }

  for (i = 0U; i < count; i++)
  {
    out_ppi_ms[i] = chronological[selected_start + i];
  }

  return count;
}

static uint32_t AF_PpiDurationMs(const uint16_t *ppi_ms, uint16_t count)
{
  uint32_t duration_ms = 0U;
  uint16_t i;

  if (ppi_ms == NULL)
  {
    return 0U;
  }

  for (i = 0U; i < count; i++)
  {
    duration_ms += ppi_ms[i];
  }

  return duration_ms;
}

static int16_t AF_RiskPercentFromPpi(const uint16_t *ppi_ms, uint16_t count)
{
  float features[AF_NB_FEATURE_COUNT];
  float risk;

  if (AF_ComputeFeaturesFromPpi(ppi_ms, count, features) == 0U)
  {
    return -1;
  }

  risk = AF_PredictRiskPercent(features);
  if (risk < 0.0f)
  {
    risk = 0.0f;
  }
  if (risk > 100.0f)
  {
    risk = 100.0f;
  }
  return PPG_RoundToInt16(risk);
}

static uint8_t AF_ComputeFeaturesFromPpi(const uint16_t *ppi_ms, uint16_t count, float *features)
{
  float intervals[AF_MAX_PPI_COUNT];
  float sorted_intervals[AF_MAX_PPI_COUNT];
  float abs_deltas[AF_MAX_PPI_COUNT];
  float sum = 0.0f;
  float mean;
  float q25;
  float q75;
  float trimmed_sum = 0.0f;
  float trimmed_mean;
  float trimmed_variance = 0.0f;
  float trimmed_std;
  float pnn50_count = 0.0f;
  float pnn80_count = 0.0f;
  float pnn120_count = 0.0f;
  uint16_t valid_count = 0U;
  uint16_t delta_count;
  uint16_t trim;
  uint16_t trimmed_start;
  uint16_t trimmed_end;
  uint16_t trimmed_count;
  uint16_t i;

  if ((ppi_ms == NULL) || (features == NULL))
  {
    return 0U;
  }

  for (i = 0U; i < count; i++)
  {
    if ((ppi_ms[i] >= 300U) && (ppi_ms[i] <= 2200U) && (valid_count < AF_MAX_PPI_COUNT))
    {
      intervals[valid_count] = (float)ppi_ms[i];
      sorted_intervals[valid_count] = intervals[valid_count];
      sum += intervals[valid_count];
      valid_count++;
    }
  }

  if (valid_count < 2U)
  {
    return 0U;
  }

  mean = sum / (float)valid_count;
  delta_count = (uint16_t)(valid_count - 1U);
  for (i = 1U; i < valid_count; i++)
  {
    float delta = intervals[i] - intervals[i - 1U];
    float abs_delta = fabsf(delta);

    abs_deltas[i - 1U] = abs_delta;
    if (abs_delta > 50.0f)
    {
      pnn50_count += 1.0f;
    }
    if (abs_delta > 80.0f)
    {
      pnn80_count += 1.0f;
    }
    if (abs_delta > 120.0f)
    {
      pnn120_count += 1.0f;
    }
  }

  AF_SortFloat(sorted_intervals, valid_count);
  AF_SortFloat(abs_deltas, delta_count);
  q25 = AF_PercentileSorted(sorted_intervals, valid_count, 0.25f);
  q75 = AF_PercentileSorted(sorted_intervals, valid_count, 0.75f);

  trim = (uint16_t)(valid_count / 10U);
  if ((valid_count >= 10U) && (trim > 0U) && ((uint16_t)(2U * trim) < valid_count))
  {
    trimmed_start = trim;
    trimmed_end = (uint16_t)(valid_count - trim);
  }
  else
  {
    trimmed_start = 0U;
    trimmed_end = valid_count;
  }
  trimmed_count = (uint16_t)(trimmed_end - trimmed_start);

  for (i = trimmed_start; i < trimmed_end; i++)
  {
    trimmed_sum += sorted_intervals[i];
  }
  trimmed_mean = (trimmed_count > 0U) ? (trimmed_sum / (float)trimmed_count) : mean;

  if (trimmed_count > 1U)
  {
    for (i = trimmed_start; i < trimmed_end; i++)
    {
      float centered = sorted_intervals[i] - trimmed_mean;
      trimmed_variance += centered * centered;
    }
    trimmed_variance /= (float)(trimmed_count - 1U);
  }
  trimmed_std = sqrtf(trimmed_variance);

  features[0] = (float)valid_count;
  features[1] = mean;
  features[2] = q75 - q25;
  features[3] = trimmed_std;
  features[4] = (trimmed_mean > 0.0f) ? (trimmed_std / trimmed_mean) : 0.0f;
  features[5] = AF_PercentileSorted(abs_deltas, delta_count, 0.50f);
  features[6] = AF_PercentileSorted(abs_deltas, delta_count, 0.80f);
  features[7] = AF_PercentileSorted(abs_deltas, delta_count, 0.95f);
  features[8] = (100.0f * pnn50_count) / (float)delta_count;
  features[9] = (100.0f * pnn80_count) / (float)delta_count;
  features[10] = (100.0f * pnn120_count) / (float)delta_count;

  return 1U;
}

static float AF_PredictRiskPercent(const float *features)
{
  float normal_score = af_nb_class_log_prior[AF_NB_CLASS_NORMAL];
  float af_score = af_nb_class_log_prior[AF_NB_CLASS_AF];
  float diff;
  uint8_t i;

  if (features == NULL)
  {
    return 0.0f;
  }

  for (i = 0U; i < AF_NB_FEATURE_COUNT; i++)
  {
    uint8_t bin = AF_BinIndex(i, features[i]);

    normal_score += af_nb_log_prob_normal[i][bin];
    af_score += af_nb_log_prob_af[i][bin];
  }

  diff = af_score - normal_score;
  if (diff >= 0.0f)
  {
    return 100.0f / (1.0f + expf(-diff));
  }
  else
  {
    float exp_value = expf(diff);
    return (100.0f * exp_value) / (1.0f + exp_value);
  }
}

static uint8_t AF_BinIndex(uint8_t feature_index, float value)
{
  uint8_t i;

  if (feature_index >= AF_NB_FEATURE_COUNT)
  {
    return 0U;
  }

  for (i = 0U; i < af_nb_edge_count[feature_index]; i++)
  {
    if (value < af_nb_bin_edges[feature_index][i])
    {
      return i;
    }
  }
  return af_nb_edge_count[feature_index];
}

static void AF_SortFloat(float *values, uint16_t count)
{
  uint16_t i;

  if (values == NULL)
  {
    return;
  }

  for (i = 1U; i < count; i++)
  {
    float key = values[i];
    int16_t j = (int16_t)i - 1;

    while ((j >= 0) && (values[j] > key))
    {
      values[j + 1] = values[j];
      j--;
    }
    values[j + 1] = key;
  }
}

static float AF_PercentileSorted(const float *sorted_values, uint16_t count, float percentile)
{
  float pos;
  uint16_t low;
  uint16_t high;
  float frac;

  if ((sorted_values == NULL) || (count == 0U))
  {
    return 0.0f;
  }
  if (count == 1U)
  {
    return sorted_values[0];
  }

  percentile = ClampFloat(percentile, 0.0f, 1.0f);
  pos = (float)(count - 1U) * percentile;
  low = (uint16_t)pos;
  high = low;
  if (((float)low < pos) && ((uint16_t)(low + 1U) < count))
  {
    high = (uint16_t)(low + 1U);
  }
  frac = pos - (float)low;

  return (sorted_values[low] * (1.0f - frac)) + (sorted_values[high] * frac);
}

static void Stress_LiveReset(void)
{
  memset(stress_live_ppi_ms, 0, sizeof(stress_live_ppi_ms));
  stress_live_ppi_count = 0U;
  stress_live_ppi_index = 0U;
  stress_live_last_peak_ms = 0U;
  stress_live_last_index_sample = 0U;
  stress_live_last_interval_ms = 0U;
  stress_live_last_interval_accepted = 0U;
  stress_live_accepted_ppi_count = 0U;
  stress_live_rejected_ppi_count = 0U;
  stress_live_quality_accept_snapshot = 0U;
  stress_live_quality_reject_snapshot = 0U;
  stress_hrv_index = -1;
}

static void Stress_LiveRecordPeak(uint32_t peak_time_ms)
{
  stress_live_last_interval_ms = 0U;
  stress_live_last_interval_accepted = 0U;

  if (stress_live_last_peak_ms != 0U)
  {
    uint32_t interval_ms = peak_time_ms - stress_live_last_peak_ms;
    stress_live_last_interval_ms = (interval_ms > 65535U) ? 65535U : (uint16_t)interval_ms;

    if (PPG_HrvPpiIsUsable(interval_ms) != 0U)
    {
      stress_live_ppi_ms[stress_live_ppi_index] = (uint16_t)interval_ms;
      stress_live_last_interval_accepted = 1U;
      stress_live_accepted_ppi_count++;
      stress_live_ppi_index++;
      if (stress_live_ppi_index >= STRESS_HRV_MAX_PPI_COUNT)
      {
        stress_live_ppi_index = 0U;
      }
      if (stress_live_ppi_count < STRESS_HRV_MAX_PPI_COUNT)
      {
        stress_live_ppi_count++;
      }
    }
    else
    {
      stress_live_rejected_ppi_count++;
    }
  }

  stress_live_last_peak_ms = peak_time_ms;
}

static void Stress_LiveUpdateIndex(void)
{
  uint16_t ppi_ms[STRESS_HRV_MAX_PPI_COUNT];
  uint16_t ppi_count;
  uint32_t ppi_duration_ms;
  uint32_t update_interval_samples;
  uint32_t accepted_since_update;
  uint32_t rejected_since_update;
  int16_t next_index = -1;

  if ((finger_present == 0U) ||
      (ppg_state.raw_finger_present == 0U) ||
      (ppg_state.finger_score < PPG_MIN_SPO2_SCORE))
  {
    if ((stress_hrv_index != -1) ||
        (stress_live_ppi_count != 0U) ||
        (stress_live_last_peak_ms != 0U))
    {
      Stress_LiveReset();
      if (app_display_mode == APP_DISPLAY_STRESS)
      {
        last_oled_vitals_update_ms = 0U;
      }
    }
    return;
  }

  if ((ppg_signal_valid == 0U) ||
      (heart_rate_bpm <= 0) ||
      (spo2_percent <= 0))
  {
    return;
  }

  update_interval_samples = ((stress_hrv_index < 0) ||
                             (heart_rate_bpm > STRESS_HRV_HIGH_HR_BPM)) ?
                            STRESS_HRV_FIRST_UPDATE_INTERVAL_SAMPLES :
                            STRESS_HRV_REFRESH_UPDATE_INTERVAL_SAMPLES;
  if ((stress_live_last_index_sample != 0U) &&
      ((max30102_samples_total - stress_live_last_index_sample) < update_interval_samples))
  {
    return;
  }
  stress_live_last_index_sample = max30102_samples_total;

  accepted_since_update = stress_live_accepted_ppi_count - stress_live_quality_accept_snapshot;
  rejected_since_update = stress_live_rejected_ppi_count - stress_live_quality_reject_snapshot;
  stress_live_quality_accept_snapshot = stress_live_accepted_ppi_count;
  stress_live_quality_reject_snapshot = stress_live_rejected_ppi_count;
  if (PPG_HrvWindowQualityOk(accepted_since_update, rejected_since_update) == 0U)
  {
    return;
  }

  ppi_count = Stress_LiveCopyPpi(ppi_ms, STRESS_HRV_MAX_PPI_COUNT);
  ppi_duration_ms = AF_PpiDurationMs(ppi_ms, ppi_count);
  if ((ppi_count < STRESS_HRV_MIN_INTERVAL_COUNT) ||
      (ppi_duration_ms < STRESS_HRV_WINDOW_TARGET_MS))
  {
    if (stress_hrv_index >= 0)
    {
      return;
    }
  }
  else
  {
    float features[STRESS_HRV_FEATURE_COUNT];

    if (Stress_ComputeFeaturesFromPpi(ppi_ms, ppi_count, features) != 0U)
    {
      next_index = Stress_IndexFromProbability(Stress_PredictProbability(features));
    }
  }

  if (next_index != stress_hrv_index)
  {
    stress_hrv_index = next_index;
    if (app_display_mode == APP_DISPLAY_STRESS)
    {
      last_oled_vitals_update_ms = 0U;
    }
  }
}

static uint16_t Stress_LiveCopyPpi(uint16_t *out_ppi_ms, uint16_t max_count)
{
  uint16_t chronological[STRESS_HRV_MAX_PPI_COUNT];
  uint16_t count;
  uint16_t start;
  uint16_t selected_start;
  uint32_t selected_duration_ms = 0U;
  uint16_t i;

  if ((out_ppi_ms == NULL) || (max_count == 0U))
  {
    return 0U;
  }

  count = stress_live_ppi_count;
  if (count > max_count)
  {
    count = max_count;
  }

  start = (stress_live_ppi_count < STRESS_HRV_MAX_PPI_COUNT) ? 0U : stress_live_ppi_index;
  for (i = 0U; i < count; i++)
  {
    uint16_t index = (uint16_t)(start + i);

    if (index >= STRESS_HRV_MAX_PPI_COUNT)
    {
      index = (uint16_t)(index - STRESS_HRV_MAX_PPI_COUNT);
    }
    chronological[i] = stress_live_ppi_ms[index];
  }

  selected_start = count;
  while ((selected_start > 0U) && (selected_duration_ms < STRESS_HRV_WINDOW_TARGET_MS))
  {
    selected_start--;
    selected_duration_ms += chronological[selected_start];
  }

  count = (uint16_t)(count - selected_start);
  if (count > max_count)
  {
    count = max_count;
  }

  for (i = 0U; i < count; i++)
  {
    out_ppi_ms[i] = chronological[selected_start + i];
  }

  return count;
}

static uint8_t Stress_ComputeFeaturesFromPpi(const uint16_t *ppi_ms, uint16_t count, float *features)
{
  float intervals[STRESS_HRV_MAX_PPI_COUNT];
  float sorted_intervals[STRESS_HRV_MAX_PPI_COUNT];
  float deltas[STRESS_HRV_MAX_PPI_COUNT];
  float abs_deltas[STRESS_HRV_MAX_PPI_COUNT];
  float sum = 0.0f;
  float mean_ppi;
  float variance = 0.0f;
  float sdnn;
  float rmssd_sum = 0.0f;
  float mean_delta = 0.0f;
  float sdsd_variance = 0.0f;
  float median_ppi;
  float outlier_low;
  float outlier_high;
  float pnn20_count = 0.0f;
  float pnn50_count = 0.0f;
  float outlier_count = 0.0f;
  uint16_t valid_count = 0U;
  uint16_t delta_count;
  uint16_t i;

  if ((ppi_ms == NULL) || (features == NULL))
  {
    return 0U;
  }

  for (i = 0U; i < count; i++)
  {
    if ((ppi_ms[i] >= 300U) && (ppi_ms[i] <= 2200U) && (valid_count < STRESS_HRV_MAX_PPI_COUNT))
    {
      intervals[valid_count] = (float)ppi_ms[i];
      sorted_intervals[valid_count] = intervals[valid_count];
      sum += intervals[valid_count];
      valid_count++;
    }
  }

  if (valid_count < STRESS_HRV_MIN_INTERVAL_COUNT)
  {
    return 0U;
  }

  mean_ppi = sum / (float)valid_count;
  if (mean_ppi <= 0.0f)
  {
    return 0U;
  }

  for (i = 0U; i < valid_count; i++)
  {
    float centered = intervals[i] - mean_ppi;
    variance += centered * centered;
  }
  variance /= (float)(valid_count - 1U);
  sdnn = sqrtf(variance);

  delta_count = (uint16_t)(valid_count - 1U);
  for (i = 1U; i < valid_count; i++)
  {
    float delta = intervals[i] - intervals[i - 1U];
    float abs_delta = fabsf(delta);

    deltas[i - 1U] = delta;
    abs_deltas[i - 1U] = abs_delta;
    rmssd_sum += delta * delta;
    mean_delta += delta;
    if (abs_delta > 20.0f)
    {
      pnn20_count += 1.0f;
    }
    if (abs_delta > 50.0f)
    {
      pnn50_count += 1.0f;
    }
  }
  mean_delta /= (float)delta_count;

  for (i = 0U; i < delta_count; i++)
  {
    float centered = deltas[i] - mean_delta;
    sdsd_variance += centered * centered;
  }
  if (delta_count > 1U)
  {
    sdsd_variance /= (float)(delta_count - 1U);
  }

  AF_SortFloat(sorted_intervals, valid_count);
  AF_SortFloat(abs_deltas, delta_count);
  median_ppi = AF_PercentileSorted(sorted_intervals, valid_count, 0.50f);
  outlier_low = median_ppi * 0.65f;
  outlier_high = median_ppi * 1.55f;
  for (i = 0U; i < valid_count; i++)
  {
    if ((intervals[i] < outlier_low) || (intervals[i] > outlier_high))
    {
      outlier_count += 1.0f;
    }
  }

  features[0] = (float)valid_count;
  features[1] = 60000.0f / mean_ppi;
  features[2] = mean_ppi;
  features[3] = sdnn;
  features[4] = sqrtf(rmssd_sum / (float)delta_count);
  features[5] = sqrtf(sdsd_variance);
  features[6] = (100.0f * pnn20_count) / (float)delta_count;
  features[7] = (100.0f * pnn50_count) / (float)delta_count;
  features[8] = sdnn / mean_ppi;
  features[9] = AF_PercentileSorted(abs_deltas, delta_count, 0.50f);
  features[10] = AF_PercentileSorted(abs_deltas, delta_count, 0.80f);
  features[11] = AF_PercentileSorted(abs_deltas, delta_count, 0.95f);
  features[12] = outlier_count / (float)valid_count;

  return 1U;
}

static float Stress_PredictProbability(const float *features)
{
  float z = stress_hrv_intercept;
  uint8_t i;

  if (features == NULL)
  {
    return 0.0f;
  }

  for (i = 0U; i < STRESS_HRV_FEATURE_COUNT; i++)
  {
    float scale = stress_hrv_scaler_scale[i];

    if (scale <= 0.0f)
    {
      scale = 1.0f;
    }
    z += stress_hrv_coef[i] * ((features[i] - stress_hrv_scaler_mean[i]) / scale);
  }

  if (z >= 0.0f)
  {
    return 1.0f / (1.0f + expf(-z));
  }
  else
  {
    float exp_value = expf(z);
    return exp_value / (1.0f + exp_value);
  }
}

static int16_t Stress_IndexFromProbability(float probability)
{
  float raw;

  probability = ClampFloat(probability, 0.0f, 1.0f);
  if (stress_hrv_prob_stress_median <= (stress_hrv_prob_nonstress_median + 0.000001f))
  {
    raw = 1.0f + (probability * 98.0f);
  }
  else
  {
    raw = stress_hrv_index_nonstress_target +
          ((probability - stress_hrv_prob_nonstress_median) *
           (stress_hrv_index_stress_target - stress_hrv_index_nonstress_target) /
           (stress_hrv_prob_stress_median - stress_hrv_prob_nonstress_median));
  }
  raw += stress_hrv_display_index_bias;
  raw = ClampFloat(raw, (float)STRESS_HRV_INDEX_MIN, (float)STRESS_HRV_INDEX_MAX);
  return PPG_RoundToInt16(raw);
}

static const char *Stress_LevelText(int16_t index)
{
  if (index < 0)
  {
    return "--";
  }
  if (index <= 29)
  {
    return "relax";
  }
  if (index <= 59)
  {
    return "normal";
  }
  if (index <= 79)
  {
    return "medium";
  }
  return "high";
}

static uint8_t CDC_Write(const uint8_t *buf, uint16_t len)
{
  uint32_t start_tick = HAL_GetTick();
  uint8_t status;

#if DIAG_STREAM_USB_ENABLE != 0U
  diag_stats.cdc_write_calls++;
#endif

  if ((buf == NULL) || (len == 0U) || (hUsbDeviceFS.dev_state != USBD_STATE_CONFIGURED))
  {
    return USBD_FAIL;
  }

  do
  {
    status = CDC_Transmit_FS((uint8_t *)buf, len);
    if (status == USBD_OK)
    {
#if DIAG_STREAM_USB_ENABLE != 0U
      uint32_t wait_ms = HAL_GetTick() - start_tick;
      diag_stats.cdc_write_total_wait_ms += wait_ms;
      Diagnostics_UpdateMax(&diag_stats.cdc_write_max_wait_ms, wait_ms);
#endif
      return USBD_OK;
    }

    if (status != USBD_BUSY)
    {
      return status;
    }

#if DIAG_STREAM_USB_ENABLE != 0U
    diag_stats.cdc_busy_count++;
#endif
    HAL_Delay(1U);
  } while ((HAL_GetTick() - start_tick) < CDC_TX_TIMEOUT_MS);

#if DIAG_STREAM_USB_ENABLE != 0U
  diag_stats.cdc_timeout_count++;
  Diagnostics_UpdateMax(&diag_stats.cdc_write_max_wait_ms, HAL_GetTick() - start_tick);
#endif

  return USBD_BUSY;
}

static void CDC_Printf(const char *fmt, ...)
{
  char msg[160];
  va_list args;
  int len;

  va_start(args, fmt);
  len = vsnprintf(msg, sizeof(msg), fmt, args);
  va_end(args);

  if (len <= 0)
  {
    return;
  }

  if ((size_t)len >= sizeof(msg))
  {
    len = (int)sizeof(msg) - 1;
  }

  if (CDC_DEBUG_LOG_TO_USB != 0U)
  {
    (void)CDC_Write((uint8_t *)msg, (uint16_t)len);
  }
  Semihosting_WriteString(msg);
}

static void Semihosting_WriteString(const char *str)
{
  if ((str == NULL) || ((CoreDebug->DHCSR & CoreDebug_DHCSR_C_DEBUGEN_Msk) == 0U))
  {
    return;
  }

  register uint32_t op __asm("r0") = 0x04U;
  register const char *arg __asm("r1") = str;

  __asm volatile ("bkpt 0xAB" : "+r" (op) : "r" (arg) : "memory");
}

static void Diagnostics_RecordLoopTick(uint32_t now_ms)
{
#if DIAG_STREAM_USB_ENABLE != 0U
  if (diag_last_loop_tick_ms != 0U)
  {
    Diagnostics_UpdateMax(&diag_stats.loop_gap_max_ms, now_ms - diag_last_loop_tick_ms);
  }
  diag_last_loop_tick_ms = now_ms;
  diag_stats.loop_count++;
#else
  (void)now_ms;
#endif
}

static void Diagnostics_ResetInterval(void)
{
  memset(&diag_stats, 0, sizeof(diag_stats));
}

static void Diagnostics_UpdateMax(uint32_t *slot, uint32_t value)
{
  if ((slot != NULL) && (value > *slot))
  {
    *slot = value;
  }
}

static void Diagnostics_WriteHeaderIfNeeded(void)
{
#if DIAG_STREAM_USB_ENABLE != 0U
  static const char header[] =
      "diag_ms,loop_gap_max_ms,loop_count,max30102_calls,max30102_samples,"
      "max30102_fifo_max,max30102_max_ms,autocorr_calls,autocorr_done,"
      "autocorr_max_ms,autocorr_active,oled091_flushes,oled091_max_ms,"
      "oled64_flushes,oled64_max_ms,cdc_calls,cdc_busy,cdc_timeout,"
      "cdc_max_wait_ms,cdc_total_wait_ms,irq_count,sample_count,finger,valid,"
      "hr,auto,spo2\r\n";

  if ((diag_stream_header_sent == 0U) && (CDC_IsPortOpen_FS() != 0U))
  {
    if (CDC_Write((const uint8_t *)header, (uint16_t)(sizeof(header) - 1U)) == USBD_OK)
    {
      diag_stream_header_sent = 1U;
    }
  }
#endif
}

static uint8_t I2C_IsReady7Bit(I2C_HandleTypeDef *hi2c, uint8_t addr7)
{
  return (HAL_I2C_IsDeviceReady(hi2c,
                                (uint16_t)(addr7 << 1),
                                I2C_SCAN_TRIALS,
                                I2C_SCAN_TIMEOUT_MS) == HAL_OK) ? 1U : 0U;
}

static void I2C_ScanBus(const char *name, I2C_HandleTypeDef *hi2c,
                        uint8_t *found_addr, volatile uint8_t *found_count)
{
  uint16_t dev_addr;

  *found_count = 0U;
  CDC_Printf("\r\n[%s] scan start\r\n", name);

  /*
   * HAL_I2C_IsDeviceReady() expects the 7-bit address shifted left by one.
   * This loop covers the 0x00..0xFF HAL address space and skips odd values
   * because those only represent the read bit of the same 7-bit address.
   */
  for (dev_addr = 0x00U; dev_addr <= 0xFFU; dev_addr++)
  {
    uint8_t addr7;

    if ((dev_addr & 0x01U) != 0U)
    {
      continue;
    }

    if (HAL_I2C_IsDeviceReady(hi2c,
                              dev_addr,
                              I2C_SCAN_TRIALS,
                              I2C_SCAN_TIMEOUT_MS) == HAL_OK)
    {
      addr7 = (uint8_t)(dev_addr >> 1);
      if (*found_count < 128U)
      {
        found_addr[*found_count] = addr7;
        (*found_count)++;
      }
      CDC_Printf("[%s] found 7-bit 0x%02X, HAL addr 0x%02X\r\n",
                 name, (unsigned int)addr7, (unsigned int)dev_addr);
    }
  }

  CDC_Printf("[%s] scan done, devices=%u\r\n", name, (unsigned int)*found_count);
}

static void I2C_RunStartupScanner(void)
{
  CDC_Printf("\r\nFW_TAG=%s\r\n", FW_TAG);
  CDC_Printf("\r\nSTM32F411 startup I2C scanner\r\n");
  CDC_Printf("Expected: I2C1 SSD1306=0x%02X, I2C2 SSD1306=0x%02X, I2C3 MAX30102=0x%02X\r\n",
             (unsigned int)SSD1306_I2C_ADDR_7BIT,
             (unsigned int)SSD1306_I2C_ADDR_7BIT,
             (unsigned int)MAX30102_I2C_ADDR_7BIT);

  I2C_ScanBus("I2C1 PB6/PB7 OLED 0.96 SSD1306", &hi2c1, i2c1_found_addr, &i2c1_found_count);
  I2C_ScanBus("I2C2 PB10/PB3 OLED 0.91 SSD1306", &hi2c2, i2c2_found_addr, &i2c2_found_count);
  I2C_ScanBus("I2C3 PA8/PB4 MAX30102", &hi2c3, i2c3_found_addr, &i2c3_found_count);

  oled_096_ready = I2C_IsReady7Bit(&hi2c1, SSD1306_I2C_ADDR_7BIT);
  oled_091_ready = I2C_IsReady7Bit(&hi2c2, SSD1306_I2C_ADDR_7BIT);
  max30102_ready = I2C_IsReady7Bit(&hi2c3, MAX30102_I2C_ADDR_7BIT);

  CDC_Printf("\r\nExpected device check:\r\n");
  CDC_Printf("I2C1 OLED 0.96 SSD1306 @0x%02X: %s\r\n",
             (unsigned int)SSD1306_I2C_ADDR_7BIT, oled_096_ready ? "OK" : "MISSING");
  CDC_Printf("I2C2 OLED 0.91 SSD1306 @0x%02X: %s\r\n",
             (unsigned int)SSD1306_I2C_ADDR_7BIT, oled_091_ready ? "OK" : "MISSING");
  CDC_Printf("I2C3 MAX30102 @0x%02X: %s\r\n",
             (unsigned int)MAX30102_I2C_ADDR_7BIT, max30102_ready ? "OK" : "MISSING");
}

static void I2C_PrintFoundList(const char *name, uint8_t *found_addr, volatile uint8_t found_count)
{
  uint8_t i;

  CDC_Printf("%s devices=%u:", name, (unsigned int)found_count);
  for (i = 0U; i < found_count; i++)
  {
    CDC_Printf(" 0x%02X", (unsigned int)found_addr[i]);
  }
  CDC_Printf("\r\n");
}

static void I2C_PrintStartupReport(void)
{
  CDC_Printf("\r\nSTM32F411 startup I2C scanner summary\r\n");
  I2C_PrintFoundList("I2C1 PB6/PB7 OLED 0.96 SSD1306", i2c1_found_addr, i2c1_found_count);
  I2C_PrintFoundList("I2C2 PB10/PB3 OLED 0.91 SSD1306", i2c2_found_addr, i2c2_found_count);
  I2C_PrintFoundList("I2C3 PA8/PB4 MAX30102", i2c3_found_addr, i2c3_found_count);

  CDC_Printf("Expected device check:\r\n");
  CDC_Printf("I2C1 OLED 0.96 SSD1306 @0x%02X: %s\r\n",
             (unsigned int)SSD1306_I2C_ADDR_7BIT, oled_096_ready ? "OK" : "MISSING");
  CDC_Printf("I2C2 OLED 0.91 SSD1306 @0x%02X: %s\r\n",
             (unsigned int)SSD1306_I2C_ADDR_7BIT, oled_091_ready ? "OK" : "MISSING");
  CDC_Printf("I2C3 MAX30102 @0x%02X: %s\r\n",
             (unsigned int)MAX30102_I2C_ADDR_7BIT, max30102_ready ? "OK" : "MISSING");
  CDC_Printf("OLED display init: I2C1=%s, I2C2=%s\r\n",
             oled_096_display_ready ? "OK" : "FAIL",
             oled_091_display_ready ? "OK" : "FAIL");
}

static HAL_StatusTypeDef OLED_WriteCommand(I2C_HandleTypeDef *hi2c, uint8_t addr7, uint8_t cmd)
{
  uint8_t tx[2] = {0x00U, cmd};

  return HAL_I2C_Master_Transmit(hi2c,
                                 (uint16_t)(addr7 << 1),
                                 tx,
                                 sizeof(tx),
                                 OLED_I2C_TIMEOUT_MS);
}

static HAL_StatusTypeDef OLED_WriteData(I2C_HandleTypeDef *hi2c, uint8_t addr7, const uint8_t *data, uint16_t len)
{
  uint8_t tx[OLED_DATA_CHUNK_BYTES + 1U];
  uint16_t offset = 0U;

  while (offset < len)
  {
    uint16_t chunk = len - offset;

    if (chunk > OLED_DATA_CHUNK_BYTES)
    {
      chunk = OLED_DATA_CHUNK_BYTES;
    }

    tx[0] = 0x40U;
    memcpy(&tx[1], &data[offset], chunk);

    if (HAL_I2C_Master_Transmit(hi2c,
                                (uint16_t)(addr7 << 1),
                                tx,
                                (uint16_t)(chunk + 1U),
                                OLED_I2C_TIMEOUT_MS) != HAL_OK)
    {
      return HAL_ERROR;
    }

    offset += chunk;
  }

  return HAL_OK;
}

static HAL_StatusTypeDef OLED_SetPageColumn(I2C_HandleTypeDef *hi2c, uint8_t addr7, uint8_t page, uint8_t column)
{
  if (OLED_WriteCommand(hi2c, addr7, (uint8_t)(0xB0U | page)) != HAL_OK)
  {
    return HAL_ERROR;
  }
  if (OLED_WriteCommand(hi2c, addr7, (uint8_t)(0x00U | (column & 0x0FU))) != HAL_OK)
  {
    return HAL_ERROR;
  }
  if (OLED_WriteCommand(hi2c, addr7, (uint8_t)(0x10U | (column >> 4))) != HAL_OK)
  {
    return HAL_ERROR;
  }

  return HAL_OK;
}

static HAL_StatusTypeDef OLED_Init(I2C_HandleTypeDef *hi2c, uint8_t addr7, uint8_t height)
{
  const uint8_t multiplex = (uint8_t)(height - 1U);
  const uint8_t compins = (height == OLED_091_HEIGHT) ? 0x02U : 0x12U;
  const uint8_t cmds[] = {
    0xAEU,
    0x20U, 0x00U,
    0xB0U,
    0xC8U,
    0x00U,
    0x10U,
    0x40U,
    0x81U, 0x7FU,
    0xA1U,
    0xA6U,
    0xA8U, multiplex,
    0xA4U,
    0xD3U, 0x00U,
    0xD5U, 0x80U,
    0xD9U, 0xF1U,
    0xDAU, compins,
    0xDBU, 0x40U,
    0x8DU, 0x14U,
    0xAFU
  };
  size_t i;

  HAL_Delay(50U);
  for (i = 0U; i < sizeof(cmds); i++)
  {
    if (OLED_WriteCommand(hi2c, addr7, cmds[i]) != HAL_OK)
    {
      return HAL_ERROR;
    }
  }

  return HAL_OK;
}

static void OLED_Clear(I2C_HandleTypeDef *hi2c, uint8_t addr7, uint8_t height)
{
  uint8_t zero[16] = {0};
  uint8_t page;

  for (page = 0U; page < (height / 8U); page++)
  {
    uint8_t col;

    if (OLED_SetPageColumn(hi2c, addr7, page, 0U) != HAL_OK)
    {
      return;
    }

    for (col = 0U; col < (OLED_WIDTH / sizeof(zero)); col++)
    {
      if (OLED_WriteData(hi2c, addr7, zero, sizeof(zero)) != HAL_OK)
      {
        return;
      }
    }
  }
}

static const uint8_t *OLED_Font5x7(char ch)
{
  static const uint8_t font_space[5] = {0x00U, 0x00U, 0x00U, 0x00U, 0x00U};
  static const uint8_t font_dash[5] = {0x08U, 0x08U, 0x08U, 0x08U, 0x08U};
  static const uint8_t font_dot[5] = {0x00U, 0x60U, 0x60U, 0x00U, 0x00U};
  static const uint8_t font_colon[5] = {0x00U, 0x36U, 0x36U, 0x00U, 0x00U};
  static const uint8_t font_percent[5] = {0x63U, 0x13U, 0x08U, 0x64U, 0x63U};
  static const uint8_t font_lbracket[5] = {0x00U, 0x7FU, 0x41U, 0x41U, 0x00U};
  static const uint8_t font_rbracket[5] = {0x00U, 0x41U, 0x41U, 0x7FU, 0x00U};
  static const uint8_t font_A[5] = {0x7EU, 0x11U, 0x11U, 0x11U, 0x7EU};
  static const uint8_t font_E[5] = {0x7FU, 0x49U, 0x49U, 0x49U, 0x41U};
  static const uint8_t font_F[5] = {0x7FU, 0x09U, 0x09U, 0x09U, 0x01U};
  static const uint8_t font_L[5] = {0x7FU, 0x40U, 0x40U, 0x40U, 0x40U};
  static const uint8_t font_P[5] = {0x7FU, 0x09U, 0x09U, 0x09U, 0x06U};
  static const uint8_t font_R[5] = {0x7FU, 0x09U, 0x19U, 0x29U, 0x46U};
  static const uint8_t font_S[5] = {0x46U, 0x49U, 0x49U, 0x49U, 0x31U};
  static const uint8_t font_T[5] = {0x01U, 0x01U, 0x7FU, 0x01U, 0x01U};
  static const uint8_t font_V[5] = {0x1FU, 0x20U, 0x40U, 0x20U, 0x1FU};
  static const uint8_t font_O[5] = {0x3EU, 0x41U, 0x41U, 0x41U, 0x3EU};
  static const uint8_t font_0[5] = {0x3EU, 0x51U, 0x49U, 0x45U, 0x3EU};
  static const uint8_t font_1[5] = {0x00U, 0x42U, 0x7FU, 0x40U, 0x00U};
  static const uint8_t font_2[5] = {0x42U, 0x61U, 0x51U, 0x49U, 0x46U};
  static const uint8_t font_3[5] = {0x21U, 0x41U, 0x45U, 0x4BU, 0x31U};
  static const uint8_t font_4[5] = {0x18U, 0x14U, 0x12U, 0x7FU, 0x10U};
  static const uint8_t font_5[5] = {0x27U, 0x45U, 0x45U, 0x45U, 0x39U};
  static const uint8_t font_6[5] = {0x3CU, 0x4AU, 0x49U, 0x49U, 0x30U};
  static const uint8_t font_7[5] = {0x01U, 0x71U, 0x09U, 0x05U, 0x03U};
  static const uint8_t font_8[5] = {0x36U, 0x49U, 0x49U, 0x49U, 0x36U};
  static const uint8_t font_9[5] = {0x06U, 0x49U, 0x49U, 0x29U, 0x1EU};
  static const uint8_t font_a[5] = {0x20U, 0x54U, 0x54U, 0x54U, 0x78U};
  static const uint8_t font_b[5] = {0x7FU, 0x48U, 0x44U, 0x44U, 0x38U};
  static const uint8_t font_c[5] = {0x38U, 0x44U, 0x44U, 0x44U, 0x20U};
  static const uint8_t font_d[5] = {0x38U, 0x44U, 0x44U, 0x48U, 0x7FU};
  static const uint8_t font_e[5] = {0x38U, 0x54U, 0x54U, 0x54U, 0x18U};
  static const uint8_t font_g[5] = {0x08U, 0x54U, 0x54U, 0x54U, 0x3CU};
  static const uint8_t font_i[5] = {0x00U, 0x44U, 0x7DU, 0x40U, 0x00U};
  static const uint8_t font_k[5] = {0x7FU, 0x10U, 0x28U, 0x44U, 0x00U};
  static const uint8_t font_l[5] = {0x00U, 0x41U, 0x7FU, 0x40U, 0x00U};
  static const uint8_t font_m[5] = {0x7CU, 0x04U, 0x18U, 0x04U, 0x78U};
  static const uint8_t font_n[5] = {0x7CU, 0x08U, 0x04U, 0x04U, 0x78U};
  static const uint8_t font_o[5] = {0x38U, 0x44U, 0x44U, 0x44U, 0x38U};
  static const uint8_t font_p[5] = {0x7CU, 0x14U, 0x14U, 0x14U, 0x08U};
  static const uint8_t font_r[5] = {0x7CU, 0x08U, 0x04U, 0x04U, 0x08U};
  static const uint8_t font_s[5] = {0x48U, 0x54U, 0x54U, 0x54U, 0x20U};
  static const uint8_t font_t[5] = {0x04U, 0x3FU, 0x44U, 0x40U, 0x20U};
  static const uint8_t font_u[5] = {0x3CU, 0x40U, 0x40U, 0x20U, 0x7CU};
  static const uint8_t font_v[5] = {0x1CU, 0x20U, 0x40U, 0x20U, 0x1CU};
  static const uint8_t font_w[5] = {0x3CU, 0x40U, 0x30U, 0x40U, 0x3CU};

  switch (ch)
  {
    case '-':
      return font_dash;
    case '.':
      return font_dot;
    case ':':
      return font_colon;
    case '%':
      return font_percent;
    case '[':
      return font_lbracket;
    case ']':
      return font_rbracket;
    case 'A':
      return font_A;
    case 'E':
      return font_E;
    case 'F':
      return font_F;
    case 'L':
      return font_L;
    case 'P':
      return font_P;
    case 'R':
      return font_R;
    case 'S':
      return font_S;
    case 'T':
      return font_T;
    case 'V':
      return font_V;
    case 'O':
      return font_O;
    case '0':
      return font_0;
    case '1':
      return font_1;
    case '2':
      return font_2;
    case '3':
      return font_3;
    case '4':
      return font_4;
    case '5':
      return font_5;
    case '6':
      return font_6;
    case '7':
      return font_7;
    case '8':
      return font_8;
    case '9':
      return font_9;
    case 'a':
      return font_a;
    case 'b':
      return font_b;
    case 'c':
      return font_c;
    case 'd':
      return font_d;
    case 'e':
      return font_e;
    case 'g':
      return font_g;
    case 'i':
      return font_i;
    case 'k':
      return font_k;
    case 'l':
      return font_l;
    case 'm':
      return font_m;
    case 'n':
      return font_n;
    case 'o':
      return font_o;
    case 'p':
      return font_p;
    case 'r':
      return font_r;
    case 's':
      return font_s;
    case 't':
      return font_t;
    case 'u':
      return font_u;
    case 'v':
      return font_v;
    case 'w':
      return font_w;
    case ' ':
    default:
      return font_space;
  }
}

static void OLED_WriteText2x(I2C_HandleTypeDef *hi2c, uint8_t addr7, uint8_t x, uint8_t page, const char *text)
{
  uint8_t cursor = x;

  while (*text != '\0')
  {
    const uint8_t *font = OLED_Font5x7(*text);
    uint8_t lower[11] = {0};
    uint8_t upper[11] = {0};
    uint8_t out = 0U;
    uint8_t col;
    uint8_t write_len;

    if (cursor >= OLED_WIDTH)
    {
      break;
    }

    for (col = 0U; col < 5U; col++)
    {
      uint16_t expanded = 0U;
      uint8_t row;

      for (row = 0U; row < 7U; row++)
      {
        if ((font[col] & (uint8_t)(1U << row)) != 0U)
        {
          expanded |= (uint16_t)(1U << (row * 2U));
          expanded |= (uint16_t)(1U << ((row * 2U) + 1U));
        }
      }

      lower[out] = (uint8_t)(expanded & 0xFFU);
      upper[out] = (uint8_t)((expanded >> 8) & 0xFFU);
      out++;
      lower[out] = (uint8_t)(expanded & 0xFFU);
      upper[out] = (uint8_t)((expanded >> 8) & 0xFFU);
      out++;
    }

    lower[out++] = 0x00U;
    write_len = out;
    if ((uint16_t)cursor + write_len > OLED_WIDTH)
    {
      write_len = (uint8_t)(OLED_WIDTH - cursor);
    }

    (void)OLED_SetPageColumn(hi2c, addr7, page, cursor);
    (void)OLED_WriteData(hi2c, addr7, lower, write_len);
    (void)OLED_SetPageColumn(hi2c, addr7, (uint8_t)(page + 1U), cursor);
    (void)OLED_WriteData(hi2c, addr7, upper, write_len);

    cursor = (uint8_t)(cursor + out);
    text++;
  }
}

static void OLED_ShowStartupText(void)
{
  if (oled_096_ready != 0U)
  {
    oled_096_display_ready = (OLED_Init(&hi2c1, SSD1306_I2C_ADDR_7BIT, OLED_096_HEIGHT) == HAL_OK) ? 1U : 0U;
    if (oled_096_display_ready != 0U)
    {
      OLED_Clear(&hi2c1, SSD1306_I2C_ADDR_7BIT, OLED_096_HEIGHT);
      OLED_WriteText2x(&hi2c1, SSD1306_I2C_ADDR_7BIT, 16U, 3U, "bruce ke");
    }
  }

  if (oled_091_ready != 0U)
  {
    oled_091_display_ready = (OLED_Init(&hi2c2, SSD1306_I2C_ADDR_7BIT, OLED_091_HEIGHT) == HAL_OK) ? 1U : 0U;
    if (oled_091_display_ready != 0U)
    {
      OLED_Clear(&hi2c2, SSD1306_I2C_ADDR_7BIT, OLED_091_HEIGHT);
      OLED_WriteText2x(&hi2c2, SSD1306_I2C_ADDR_7BIT, 3U, 1U, "V3.0 05-28");
    }
  }

  CDC_Printf("OLED show text: I2C1=%s, I2C2=%s\r\n",
             oled_096_display_ready ? "OK" : "FAIL",
             oled_091_display_ready ? "OK" : "FAIL");
}

static void OLED64_ClearBuffer(void)
{
  memset(oled64_buffer, 0, sizeof(oled64_buffer));
}

static void OLED64_Flush(void)
{
  uint32_t start_ms;
  uint8_t page;

  if (oled_096_display_ready == 0U)
  {
    return;
  }

  start_ms = HAL_GetTick();
  for (page = 0U; page < (OLED_096_HEIGHT / 8U); page++)
  {
    (void)OLED_SetPageColumn(&hi2c1, SSD1306_I2C_ADDR_7BIT, page, 0U);
    (void)OLED_WriteData(&hi2c1,
                         SSD1306_I2C_ADDR_7BIT,
                         &oled64_buffer[page * OLED_WIDTH],
                         OLED_WIDTH);
  }
#if DIAG_STREAM_USB_ENABLE != 0U
  diag_stats.oled64_flush_count++;
  Diagnostics_UpdateMax(&diag_stats.oled64_flush_max_ms, HAL_GetTick() - start_ms);
#endif
}

static void OLED64_DrawPixel(int16_t x, int16_t y, uint8_t on)
{
  uint16_t index;
  uint8_t mask;

  if ((x < 0) || (x >= (int16_t)OLED_WIDTH) || (y < 0) || (y >= (int16_t)OLED_096_HEIGHT))
  {
    return;
  }

  index = (uint16_t)(((uint16_t)y / 8U) * OLED_WIDTH + (uint16_t)x);
  mask = (uint8_t)(1U << ((uint16_t)y & 0x07U));

  if (on != 0U)
  {
    oled64_buffer[index] |= mask;
  }
  else
  {
    oled64_buffer[index] &= (uint8_t)~mask;
  }
}

static void OLED64_DrawLine(int16_t x0, int16_t y0, int16_t x1, int16_t y1, uint8_t on)
{
  int16_t dx = (x0 < x1) ? (int16_t)(x1 - x0) : (int16_t)(x0 - x1);
  int16_t sx = (x0 < x1) ? 1 : -1;
  int16_t dy = (y0 < y1) ? (int16_t)(y0 - y1) : (int16_t)(y1 - y0);
  int16_t sy = (y0 < y1) ? 1 : -1;
  int16_t err = (int16_t)(dx + dy);

  while (1)
  {
    int16_t e2;

    OLED64_DrawPixel(x0, y0, on);
    if ((x0 == x1) && (y0 == y1))
    {
      break;
    }

    e2 = (int16_t)(2 * err);
    if (e2 >= dy)
    {
      err = (int16_t)(err + dy);
      x0 = (int16_t)(x0 + sx);
    }
    if (e2 <= dx)
    {
      err = (int16_t)(err + dx);
      y0 = (int16_t)(y0 + sy);
    }
  }
}

static void OLED64_FillRect(uint8_t x, uint8_t y, uint8_t w, uint8_t h, uint8_t on)
{
  uint8_t yy;

  for (yy = 0U; yy < h; yy++)
  {
    uint8_t xx;

    for (xx = 0U; xx < w; xx++)
    {
      OLED64_DrawPixel((int16_t)(x + xx), (int16_t)(y + yy), on);
    }
  }
}

static void OLED64_DrawChar5x7(uint8_t x, uint8_t y, char ch, uint8_t scale)
{
  const uint8_t *font = OLED_Font5x7(ch);
  uint8_t col;

  for (col = 0U; col < 5U; col++)
  {
    uint8_t row;

    for (row = 0U; row < 7U; row++)
    {
      if ((font[col] & (uint8_t)(1U << row)) != 0U)
      {
        OLED64_FillRect((uint8_t)(x + col * scale),
                        (uint8_t)(y + row * scale),
                        scale,
                        scale,
                        1U);
      }
    }
  }
}

static void OLED64_DrawText5x7(uint8_t x, uint8_t y, const char *text, uint8_t scale)
{
  uint8_t cursor = x;

  while (*text != '\0')
  {
    OLED64_DrawChar5x7(cursor, y, *text, scale);
    cursor = (uint8_t)(cursor + 6U * scale);
    text++;
  }
}

static void OLED64_DrawHeartIcon(uint8_t x, uint8_t y, uint8_t filled)
{
  static const char *const heart_filled[] = {
    " **   ** ",
    "**** ****",
    "*********",
    "*********",
    " ******* ",
    "  *****  ",
    "   ***   ",
    "    *    "
  };
  static const char *const heart_outline[] = {
    " **   ** ",
    "*  * *  *",
    "*   *   *",
    " *     * ",
    "  *   *  ",
    "   * *   ",
    "    *    ",
    "         "
  };
  const char *const *pattern = (filled != 0U) ? heart_filled : heart_outline;
  uint8_t row;

  for (row = 0U; row < 8U; row++)
  {
    uint8_t col;

    for (col = 0U; col < 9U; col++)
    {
      if (pattern[row][col] != ' ')
      {
        OLED64_DrawPixel((int16_t)(x + col), (int16_t)(y + row), 1U);
      }
    }
  }
}

static void OLED64_DrawHeartRateLabel(uint8_t x, uint8_t y)
{
  OLED64_FillRect((uint8_t)(x + 3U), (uint8_t)(y + 9U), 2U, 2U, 1U);
  OLED64_DrawLine((int16_t)(x + 8U), (int16_t)(y + 4U), (int16_t)(x + 6U), (int16_t)(y + 13U), 1U);
  OLED64_DrawLine((int16_t)(x + 6U), (int16_t)(y + 13U), (int16_t)(x + 11U), (int16_t)(y + 10U), 1U);
  OLED64_FillRect((uint8_t)(x + 12U), (uint8_t)(y + 8U), 2U, 2U, 1U);

  x = (uint8_t)(x + 17U);
  OLED64_DrawLine((int16_t)(x + 2U), (int16_t)(y + 1U), (int16_t)(x + 13U), (int16_t)(y + 1U), 1U);
  OLED64_DrawLine((int16_t)(x + 8U), (int16_t)y, (int16_t)(x + 8U), (int16_t)(y + 4U), 1U);
  OLED64_DrawLine((int16_t)(x + 4U), (int16_t)(y + 5U), (int16_t)(x + 7U), (int16_t)(y + 8U), 1U);
  OLED64_DrawLine((int16_t)(x + 12U), (int16_t)(y + 5U), (int16_t)(x + 9U), (int16_t)(y + 8U), 1U);
  OLED64_DrawLine((int16_t)(x + 2U), (int16_t)(y + 10U), (int16_t)(x + 14U), (int16_t)(y + 10U), 1U);
  OLED64_DrawLine((int16_t)(x + 8U), (int16_t)(y + 10U), (int16_t)(x + 8U), (int16_t)(y + 15U), 1U);
  OLED64_DrawLine((int16_t)(x + 4U), (int16_t)(y + 14U), (int16_t)(x + 12U), (int16_t)(y + 14U), 1U);

  OLED64_DrawChar5x7((uint8_t)(x + 17U), (uint8_t)(y + 3U), ':', 2U);
}

static void OLED64_DrawSpo2Label(uint8_t x, uint8_t y)
{
  OLED64_DrawLine((int16_t)(x + 8U), (int16_t)y, (int16_t)(x + 5U), (int16_t)(y + 3U), 1U);
  OLED64_DrawLine((int16_t)(x + 2U), (int16_t)(y + 4U), (int16_t)(x + 14U), (int16_t)(y + 4U), 1U);
  OLED64_DrawLine((int16_t)(x + 2U), (int16_t)(y + 4U), (int16_t)(x + 2U), (int16_t)(y + 13U), 1U);
  OLED64_DrawLine((int16_t)(x + 14U), (int16_t)(y + 4U), (int16_t)(x + 14U), (int16_t)(y + 13U), 1U);
  OLED64_DrawLine((int16_t)(x + 5U), (int16_t)(y + 5U), (int16_t)(x + 5U), (int16_t)(y + 12U), 1U);
  OLED64_DrawLine((int16_t)(x + 8U), (int16_t)(y + 5U), (int16_t)(x + 8U), (int16_t)(y + 12U), 1U);
  OLED64_DrawLine((int16_t)(x + 11U), (int16_t)(y + 5U), (int16_t)(x + 11U), (int16_t)(y + 12U), 1U);
  OLED64_DrawLine((int16_t)(x + 1U), (int16_t)(y + 15U), (int16_t)(x + 15U), (int16_t)(y + 15U), 1U);

  x = (uint8_t)(x + 17U);
  OLED64_DrawLine((int16_t)(x + 4U), (int16_t)(y + 1U), (int16_t)(x + 2U), (int16_t)(y + 4U), 1U);
  OLED64_DrawLine((int16_t)(x + 4U), (int16_t)(y + 2U), (int16_t)(x + 13U), (int16_t)(y + 2U), 1U);
  OLED64_DrawLine((int16_t)(x + 5U), (int16_t)(y + 5U), (int16_t)(x + 14U), (int16_t)(y + 5U), 1U);
  OLED64_DrawLine((int16_t)(x + 13U), (int16_t)(y + 5U), (int16_t)(x + 11U), (int16_t)(y + 8U), 1U);
  OLED64_DrawLine((int16_t)(x + 5U), (int16_t)(y + 9U), (int16_t)(x + 12U), (int16_t)(y + 9U), 1U);
  OLED64_DrawLine((int16_t)(x + 4U), (int16_t)(y + 12U), (int16_t)(x + 13U), (int16_t)(y + 12U), 1U);
  OLED64_DrawLine((int16_t)(x + 8U), (int16_t)(y + 8U), (int16_t)(x + 8U), (int16_t)(y + 15U), 1U);
  OLED64_DrawLine((int16_t)(x + 5U), (int16_t)(y + 15U), (int16_t)(x + 12U), (int16_t)(y + 15U), 1U);

  OLED64_DrawChar5x7((uint8_t)(x + 17U), (uint8_t)(y + 3U), ':', 2U);
}

static void OLED64_DrawCenteredText5x7(uint8_t y, const char *text, uint8_t scale)
{
  size_t len;
  uint16_t width;
  uint8_t x;

  if ((text == NULL) || (scale == 0U))
  {
    return;
  }

  len = strlen(text);
  width = (uint16_t)(len * 6U * scale);
  x = (width >= OLED_WIDTH) ? 0U : (uint8_t)((OLED_WIDTH - width) / 2U);
  OLED64_DrawText5x7(x, y, text, scale);
}

static void OLED64_RenderAfRisk(uint8_t test_mode)
{
  char risk_text[20];
  int16_t risk = (test_mode != 0U) ? af_test_risk_percent : af_risk_percent;
  uint16_t test_ppi_count = (uint16_t)(sizeof(af_test_ppi_ms) / sizeof(af_test_ppi_ms[0]));

  OLED64_ClearBuffer();

  if ((test_mode != 0U) && (test_ppi_count > 0U))
  {
    OLED64_DrawCenteredText5x7(7U, "[test]", 2U);
  }

  if (risk >= 0)
  {
    if (risk > 100)
    {
      risk = 100;
    }
    (void)snprintf(risk_text, sizeof(risk_text), "AF %d%%", (int)risk);
  }
  else
  {
    (void)snprintf(risk_text, sizeof(risk_text), "AF --%%");
  }

  OLED64_DrawCenteredText5x7((test_mode != 0U) ? 38U : 25U, risk_text, 2U);
  heart_flash_active = 0U;
  OLED64_Flush();
}

static void OLED64_RenderStress(void)
{
  char detail_text[20];

  OLED64_ClearBuffer();

  if (heart_rate_bpm > STRESS_HRV_HIGH_HR_BPM)
  {
    (void)snprintf(detail_text, sizeof(detail_text), "-- high HR");
  }
  else if (stress_hrv_index >= 0)
  {
    (void)snprintf(detail_text,
                   sizeof(detail_text),
                   "%d %s",
                   (int)stress_hrv_index,
                   Stress_LevelText(stress_hrv_index));
  }
  else
  {
    (void)snprintf(detail_text, sizeof(detail_text), "-- --");
  }

  OLED64_DrawCenteredText5x7(5U, "STRESS", 2U);
  OLED64_DrawCenteredText5x7(36U, detail_text, 2U);
  heart_flash_active = 0U;
  OLED64_Flush();
}

static void OLED64_RenderVitals(void)
{
  char hr_text[12];
  char spo2_text[12];

  if (app_display_mode == APP_DISPLAY_AF_LIVE)
  {
    OLED64_RenderAfRisk(0U);
    return;
  }
  if (app_display_mode == APP_DISPLAY_STRESS)
  {
    OLED64_RenderStress();
    return;
  }
  if (app_display_mode == APP_DISPLAY_AF_TEST)
  {
    OLED64_RenderAfRisk(1U);
    return;
  }

  OLED64_ClearBuffer();

  if ((finger_present != 0U) && (heart_rate_bpm > 0))
  {
    (void)snprintf(hr_text, sizeof(hr_text), "%3d", (int)heart_rate_bpm);
  }
  else
  {
    (void)snprintf(hr_text, sizeof(hr_text), "--");
  }

  if ((finger_present != 0U) && (spo2_percent > 0))
  {
    (void)snprintf(spo2_text, sizeof(spo2_text), "%2d%%", (int)spo2_percent);
  }
  else
  {
    (void)snprintf(spo2_text, sizeof(spo2_text), "--%%");
  }

  OLED64_DrawText5x7(22U, 11U, "bpm:", 2U);
  OLED64_DrawText5x7(76U, 11U, hr_text, 2U);
  OLED64_DrawText5x7(10U, 39U, "SpO2:", 2U);
  OLED64_DrawText5x7(76U, 39U, spo2_text, 2U);
  heart_flash_active = 0U;

  OLED64_Flush();
}

static void OLED091_WaveformReset(void)
{
  uint8_t i;

  memset(&oled091_waveform, 0, sizeof(oled091_waveform));
  for (i = 0U; i < OLED_WIDTH; i++)
  {
    oled091_waveform.columns[i] = OLED091_WAVEFORM_NONE;
  }
  oled091_waveform.last_peak_display_px = (uint8_t)(OLED091_WAVEFORM_AMP_Y - 1U);
  oled091_waveform.active_column_count = 0U;
  oled091_waveform.dirty = 0U;
  OLED091_ClearBuffer();
}

static void OLED091_WaveformService(uint32_t now_ms)
{
  uint8_t mode;

  if (oled_091_display_ready == 0U)
  {
    return;
  }

  mode = OLED091_DisplayMode();
  if (mode != oled091_waveform.display_mode)
  {
    oled091_waveform.display_mode = mode;
    oled091_waveform.dirty = 1U;
    if (mode == OLED091_MODE_LOADING)
    {
      oled091_waveform.loading_phase = 0U;
      oled091_waveform.last_status_anim_ms = now_ms + OLED091_STATUS_ANIM_MS;
    }
    else if (mode == OLED091_MODE_PAUSE)
    {
      oled091_waveform.loading_phase = 0U;
    }
  }

  if ((mode == OLED091_MODE_LOADING) &&
      ((int32_t)(now_ms - oled091_waveform.last_status_anim_ms) >= 0))
  {
    oled091_waveform.last_status_anim_ms = now_ms + OLED091_STATUS_ANIM_MS;
    oled091_waveform.loading_phase = (uint8_t)((oled091_waveform.loading_phase + 1U) & 0x03U);
    oled091_waveform.dirty = 1U;
  }

  if (oled091_waveform.dirty == 0U)
  {
    return;
  }
  if (max30102_int_pending != 0U)
  {
    return;
  }

  if ((int32_t)(now_ms - oled091_waveform.last_flush_ms) < 0)
  {
    return;
  }
  oled091_waveform.last_flush_ms = now_ms + OLED091_WAVEFORM_FLUSH_MS;

  if (mode == OLED091_MODE_WAVEFORM)
  {
    OLED091_WaveformRenderBuffer();
  }
  else
  {
    OLED091_RenderStatus(mode);
  }
  OLED091_Flush();
  oled091_waveform.dirty = 0U;

  if ((mode == OLED091_MODE_WAVEFORM) &&
      (oled091_waveform.draining != 0U) &&
      (oled091_waveform.active_column_count == 0U))
  {
    oled091_waveform.draining = 0U;
    oled091_waveform.display_mode = OLED091_MODE_NONE;
    oled091_waveform.dirty = 1U;
  }
}

static void OLED091_WaveformUpdate(uint32_t sample_count, uint8_t beat_peak_detected)
{
  uint8_t should_stream;
  uint32_t pending_samples;

  should_stream = ((finger_present != 0U) &&
                   (ppg_signal_valid != 0U) &&
                   (heart_rate_bpm > 0) &&
                   (ppg_state.hr_confirmed != 0U)) ? 1U : 0U;

  if (should_stream != 0U)
  {
    if (oled091_waveform.streaming == 0U)
    {
      uint8_t i;

      for (i = 0U; i < OLED_WIDTH; i++)
      {
        oled091_waveform.columns[i] = OLED091_WAVEFORM_NONE;
      }
      oled091_waveform.streaming = 1U;
      oled091_waveform.draining = 0U;
      oled091_waveform.last_column_sample = sample_count;
      oled091_waveform.last_peak_sample = 0U;
      oled091_waveform.last_peak_display_px = (uint8_t)(OLED091_WAVEFORM_AMP_Y - 1U);
      oled091_waveform.peak_floor = 0.0f;
      oled091_waveform.peak_ceil = 0.0f;
      oled091_waveform.active_column_count = 0U;
      oled091_waveform.trigger_above_threshold = 0U;
      oled091_waveform.last_ir_filt = ppg_state.ir_filt;
      oled091_waveform.dirty = 1U;
    }

    if (beat_peak_detected != 0U)
    {
      float peak_value = fabsf(ppg_robust_peak.last_peak_value);

      if (peak_value < 1.0f)
      {
        peak_value = fabsf(ppg_state.ir_filt);
      }

      if ((oled091_waveform.last_peak_sample == 0U) ||
          ((sample_count - oled091_waveform.last_peak_sample) >=
           (PPG_MIN_PEAK_MS / MAX30102_SAMPLE_INTERVAL_MS)))
      {
        oled091_waveform.last_peak_sample = sample_count;
        oled091_waveform.last_peak_display_px = OLED091_WaveformPeakHeightPx(peak_value);
      }
    }
  }
  else
  {
    if ((oled091_waveform.streaming != 0U) ||
        (oled091_waveform.active_column_count != 0U))
    {
      oled091_waveform.draining = 1U;
      oled091_waveform.dirty = 1U;
    }
    oled091_waveform.streaming = 0U;
    oled091_waveform.trigger_above_threshold = 0U;
    oled091_waveform.last_ir_filt = 0.0f;
  }

  if (oled091_waveform.last_column_sample == 0U)
  {
    oled091_waveform.last_column_sample = sample_count;
  }

  pending_samples = sample_count - oled091_waveform.last_column_sample;
  if (pending_samples > (OLED091_WAVEFORM_COLUMN_SAMPLES * OLED091_WAVEFORM_MAX_ADVANCE_COLUMNS))
  {
    oled091_waveform.last_column_sample =
        sample_count - (OLED091_WAVEFORM_COLUMN_SAMPLES * OLED091_WAVEFORM_MAX_ADVANCE_COLUMNS);
  }

  while ((sample_count - oled091_waveform.last_column_sample) >= OLED091_WAVEFORM_COLUMN_SAMPLES)
  {
    uint8_t y = OLED091_WAVEFORM_NONE;

    oled091_waveform.last_column_sample += OLED091_WAVEFORM_COLUMN_SAMPLES;
    if (oled091_waveform.streaming != 0U)
    {
      y = OLED091_WaveformPulseY(oled091_waveform.last_column_sample);
    }
    OLED091_WaveformShiftInsert(y);
  }
}

static void OLED091_WaveformShiftInsert(uint8_t y)
{
  int16_t x;
  uint8_t removed = oled091_waveform.columns[OLED_WIDTH - 1U];

  if ((removed != OLED091_WAVEFORM_NONE) &&
      (oled091_waveform.active_column_count > 0U))
  {
    oled091_waveform.active_column_count--;
  }

  for (x = (int16_t)OLED_WIDTH - 1; x > 0; x--)
  {
    oled091_waveform.columns[x] = oled091_waveform.columns[x - 1];
  }
  oled091_waveform.columns[0] = y;
  if ((y != OLED091_WAVEFORM_NONE) &&
      (oled091_waveform.active_column_count < OLED_WIDTH))
  {
    oled091_waveform.active_column_count++;
  }
  oled091_waveform.dirty = 1U;
}

static uint8_t OLED091_WaveformPulseY(uint32_t sample_count)
{
  float dt_ms;
  float value = 0.0f;
  float y_float;
  uint8_t peak_px = oled091_waveform.last_peak_display_px;

  if ((oled091_waveform.last_peak_sample == 0U) ||
      (sample_count < oled091_waveform.last_peak_sample))
  {
    return OLED091_WAVEFORM_MID_Y;
  }

  dt_ms = (float)(sample_count - oled091_waveform.last_peak_sample) *
          (float)MAX30102_SAMPLE_INTERVAL_MS;
  if (dt_ms > 520.0f)
  {
    return OLED091_WAVEFORM_MID_Y;
  }

  if (peak_px == 0U)
  {
    peak_px = (uint8_t)(OLED091_WAVEFORM_AMP_Y - 1U);
  }

  if (dt_ms <= 30.0f)
  {
    value = 1.0f - (0.10f * (dt_ms / 30.0f));
  }
  else if (dt_ms <= 90.0f)
  {
    value = 0.90f + ((-0.55f - 0.90f) * ((dt_ms - 30.0f) / 60.0f));
  }
  else if (dt_ms <= 180.0f)
  {
    value = -0.55f + ((0.20f + 0.55f) * ((dt_ms - 90.0f) / 90.0f));
  }
  else if (dt_ms <= 320.0f)
  {
    value = 0.20f * (1.0f - ((dt_ms - 180.0f) / 140.0f));
  }

  y_float = (float)OLED091_WAVEFORM_MID_Y - (value * (float)peak_px);
  return ClampU8(PPG_RoundToInt16(y_float), OLED091_WAVEFORM_MIN_Y, OLED091_WAVEFORM_MAX_Y);
}

static uint8_t OLED091_WaveformPeakHeightPx(float peak_value)
{
  float ratio;
  uint8_t min_px = (uint8_t)((OLED091_WAVEFORM_AMP_Y - 1U) - OLED091_WAVEFORM_PEAK_RANGE_PX);
  uint8_t max_px = (uint8_t)(OLED091_WAVEFORM_AMP_Y - 1U);

  if (min_px < 4U)
  {
    min_px = 4U;
  }

  if (peak_value <= 1.0f)
  {
    return max_px;
  }

  if ((oled091_waveform.peak_floor <= 0.0f) ||
      (oled091_waveform.peak_ceil <= oled091_waveform.peak_floor))
  {
    oled091_waveform.peak_floor = peak_value * 0.90f;
    oled091_waveform.peak_ceil = peak_value * 1.10f;
  }
  else
  {
    if (peak_value < oled091_waveform.peak_floor)
    {
      oled091_waveform.peak_floor = peak_value;
    }
    else
    {
      oled091_waveform.peak_floor += 0.02f * (peak_value - oled091_waveform.peak_floor);
    }

    if (peak_value > oled091_waveform.peak_ceil)
    {
      oled091_waveform.peak_ceil = peak_value;
    }
    else
    {
      oled091_waveform.peak_ceil += 0.02f * (peak_value - oled091_waveform.peak_ceil);
    }
  }

  if ((oled091_waveform.peak_ceil - oled091_waveform.peak_floor) < 1.0f)
  {
    return max_px;
  }

  ratio = (peak_value - oled091_waveform.peak_floor) /
          (oled091_waveform.peak_ceil - oled091_waveform.peak_floor);
  ratio = ClampFloat(ratio, 0.0f, 1.0f);
  return ClampU8(PPG_RoundToInt16((float)min_px + (ratio * (float)(max_px - min_px))),
                 min_px,
                 max_px);
}

static uint8_t OLED091_DisplayMode(void)
{
  if ((oled091_waveform.streaming != 0U) ||
      (oled091_waveform.draining != 0U) ||
      (oled091_waveform.active_column_count != 0U))
  {
    return OLED091_MODE_WAVEFORM;
  }

  if (finger_present != 0U)
  {
    return OLED091_MODE_LOADING;
  }

  return OLED091_MODE_PAUSE;
}

static void OLED091_WaveformRenderBuffer(void)
{
  int16_t prev_x = -1;
  int16_t prev_y = -1;
  uint8_t x;

  OLED091_ClearBuffer();

  if ((oled091_waveform.streaming != 0U) ||
      (oled091_waveform.active_column_count != 0U))
  {
    for (x = 0U; x < OLED_WIDTH; x = (uint8_t)(x + 8U))
    {
      OLED091_DrawPixel((int16_t)x, OLED091_WAVEFORM_MID_Y, 1U);
    }
  }

  for (x = 0U; x < OLED_WIDTH; x++)
  {
    uint8_t y = oled091_waveform.columns[x];

    if (y == OLED091_WAVEFORM_NONE)
    {
      prev_x = -1;
      prev_y = -1;
      continue;
    }

    if (prev_x >= 0)
    {
      OLED091_DrawLine(prev_x, prev_y, (int16_t)x, (int16_t)y, 1U);
    }
    else
    {
      OLED091_DrawPixel((int16_t)x, (int16_t)y, 1U);
    }

    prev_x = (int16_t)x;
    prev_y = (int16_t)y;
  }
}

static void OLED091_RenderStatus(uint8_t mode)
{
  OLED091_ClearBuffer();

  if (mode == OLED091_MODE_LOADING)
  {
    char text[] = "Loading   ";
    uint8_t i;

    for (i = 0U; i < oled091_waveform.loading_phase; i++)
    {
      text[7U + i] = '.';
    }
    OLED091_DrawText5x7(4U, 9U, text, 2U);
  }
  else
  {
    OLED091_DrawText5x7(34U, 9U, "Pause", 2U);
  }
}

static void OLED091_ClearBuffer(void)
{
  memset(oled32_buffer, 0, sizeof(oled32_buffer));
}

static void OLED091_Flush(void)
{
  uint32_t start_ms;
  uint8_t page;

  if (oled_091_display_ready == 0U)
  {
    return;
  }

  start_ms = HAL_GetTick();
  for (page = 0U; page < (OLED_091_HEIGHT / 8U); page++)
  {
    (void)OLED_SetPageColumn(&hi2c2, SSD1306_I2C_ADDR_7BIT, page, 0U);
    (void)OLED_WriteData(&hi2c2,
                         SSD1306_I2C_ADDR_7BIT,
                         &oled32_buffer[page * OLED_WIDTH],
                         OLED_WIDTH);
  }
#if DIAG_STREAM_USB_ENABLE != 0U
  diag_stats.oled091_flush_count++;
  Diagnostics_UpdateMax(&diag_stats.oled091_flush_max_ms, HAL_GetTick() - start_ms);
#endif
}

static void OLED091_DrawPixel(int16_t x, int16_t y, uint8_t on)
{
  uint16_t index;
  uint8_t mask;

  if ((x < 0) || (x >= (int16_t)OLED_WIDTH) || (y < 0) || (y >= (int16_t)OLED_091_HEIGHT))
  {
    return;
  }

  index = (uint16_t)(((uint16_t)y / 8U) * OLED_WIDTH + (uint16_t)x);
  mask = (uint8_t)(1U << ((uint16_t)y & 0x07U));

  if (on != 0U)
  {
    oled32_buffer[index] |= mask;
  }
  else
  {
    oled32_buffer[index] &= (uint8_t)~mask;
  }
}

static void OLED091_DrawLine(int16_t x0, int16_t y0, int16_t x1, int16_t y1, uint8_t on)
{
  int16_t dx = (x0 < x1) ? (int16_t)(x1 - x0) : (int16_t)(x0 - x1);
  int16_t sx = (x0 < x1) ? 1 : -1;
  int16_t dy = (y0 < y1) ? (int16_t)(y0 - y1) : (int16_t)(y1 - y0);
  int16_t sy = (y0 < y1) ? 1 : -1;
  int16_t err = (int16_t)(dx + dy);

  while (1)
  {
    int16_t e2;

    OLED091_DrawPixel(x0, y0, on);
    if ((x0 == x1) && (y0 == y1))
    {
      break;
    }

    e2 = (int16_t)(2 * err);
    if (e2 >= dy)
    {
      err = (int16_t)(err + dy);
      x0 = (int16_t)(x0 + sx);
    }
    if (e2 <= dx)
    {
      err = (int16_t)(err + dx);
      y0 = (int16_t)(y0 + sy);
    }
  }
}

static void OLED091_FillRect(uint8_t x, uint8_t y, uint8_t w, uint8_t h, uint8_t on)
{
  uint8_t yy;

  for (yy = 0U; yy < h; yy++)
  {
    uint8_t xx;

    for (xx = 0U; xx < w; xx++)
    {
      OLED091_DrawPixel((int16_t)(x + xx), (int16_t)(y + yy), on);
    }
  }
}

static void OLED091_DrawChar5x7(uint8_t x, uint8_t y, char ch, uint8_t scale)
{
  const uint8_t *font = OLED_Font5x7(ch);
  uint8_t col;

  for (col = 0U; col < 5U; col++)
  {
    uint8_t row;

    for (row = 0U; row < 7U; row++)
    {
      if ((font[col] & (uint8_t)(1U << row)) != 0U)
      {
        OLED091_FillRect((uint8_t)(x + col * scale),
                         (uint8_t)(y + row * scale),
                         scale,
                         scale,
                         1U);
      }
    }
  }
}

static void OLED091_DrawText5x7(uint8_t x, uint8_t y, const char *text, uint8_t scale)
{
  uint8_t cursor = x;

  while (*text != '\0')
  {
    OLED091_DrawChar5x7(cursor, y, *text, scale);
    cursor = (uint8_t)(cursor + 6U * scale);
    text++;
  }
}

static HAL_StatusTypeDef MAX30102_WriteReg(uint8_t reg, uint8_t value)
{
  return HAL_I2C_Mem_Write(&hi2c3,
                           (uint16_t)(MAX30102_I2C_ADDR_7BIT << 1),
                           reg,
                           I2C_MEMADD_SIZE_8BIT,
                           &value,
                           1U,
                           OLED_I2C_TIMEOUT_MS);
}

static HAL_StatusTypeDef MAX30102_ReadReg(uint8_t reg, uint8_t *value)
{
  return HAL_I2C_Mem_Read(&hi2c3,
                          (uint16_t)(MAX30102_I2C_ADDR_7BIT << 1),
                          reg,
                          I2C_MEMADD_SIZE_8BIT,
                          value,
                          1U,
                          OLED_I2C_TIMEOUT_MS);
}

static void MAX30102_ClearInterrupts(void)
{
  uint8_t dummy;

  (void)MAX30102_ReadReg(MAX30102_REG_INTR_STATUS_1, &dummy);
  (void)MAX30102_ReadReg(MAX30102_REG_INTR_STATUS_2, &dummy);
}

static HAL_StatusTypeDef MAX30102_InitSensor(void)
{
  uint8_t part_id = 0U;
  uint8_t mode = 0U;
  uint8_t retry;

  if (max30102_ready == 0U)
  {
    return HAL_ERROR;
  }

  if (MAX30102_ReadReg(MAX30102_REG_PART_ID, &part_id) != HAL_OK)
  {
    return HAL_ERROR;
  }

  if (part_id != MAX30102_PART_ID)
  {
    CDC_Printf("MAX30102 unexpected PART_ID=0x%02X\r\n", (unsigned int)part_id);
    return HAL_ERROR;
  }

  if (MAX30102_WriteReg(MAX30102_REG_MODE_CONFIG, 0x40U) != HAL_OK)
  {
    return HAL_ERROR;
  }

  for (retry = 0U; retry < 20U; retry++)
  {
    HAL_Delay(10U);
    if (MAX30102_ReadReg(MAX30102_REG_MODE_CONFIG, &mode) != HAL_OK)
    {
      return HAL_ERROR;
    }
    if ((mode & 0x40U) == 0U)
    {
      break;
    }
  }

  (void)MAX30102_WriteReg(MAX30102_REG_FIFO_WR_PTR, 0x00U);
  (void)MAX30102_WriteReg(MAX30102_REG_OVF_COUNTER, 0x00U);
  (void)MAX30102_WriteReg(MAX30102_REG_FIFO_RD_PTR, 0x00U);

  if (MAX30102_WriteReg(MAX30102_REG_INTR_ENABLE_1, 0x40U) != HAL_OK)
  {
    return HAL_ERROR;
  }
  if (MAX30102_WriteReg(MAX30102_REG_INTR_ENABLE_2, 0x00U) != HAL_OK)
  {
    return HAL_ERROR;
  }
  if (MAX30102_WriteReg(MAX30102_REG_FIFO_CONFIG, 0x10U) != HAL_OK)
  {
    return HAL_ERROR;
  }
  if (MAX30102_WriteReg(MAX30102_REG_SPO2_CONFIG, 0x47U) != HAL_OK)
  {
    return HAL_ERROR;
  }
  if (MAX30102_WriteReg(MAX30102_REG_LED1_PA, 0x24U) != HAL_OK)
  {
    return HAL_ERROR;
  }
  if (MAX30102_WriteReg(MAX30102_REG_LED2_PA, 0x24U) != HAL_OK)
  {
    return HAL_ERROR;
  }
  if (MAX30102_WriteReg(MAX30102_REG_PILOT_PA, 0x1FU) != HAL_OK)
  {
    return HAL_ERROR;
  }
  if (MAX30102_WriteReg(MAX30102_REG_MODE_CONFIG, 0x03U) != HAL_OK)
  {
    return HAL_ERROR;
  }

  MAX30102_ClearInterrupts();
  last_max30102_irq_ms = 0U;
  next_max30102_poll_ms = HAL_GetTick() + MAX30102_FALLBACK_POLL_MS;
  return HAL_OK;
}

static uint8_t MAX30102_FIFOCount(void)
{
  uint8_t wr = 0U;
  uint8_t rd = 0U;

  if (MAX30102_ReadReg(MAX30102_REG_FIFO_WR_PTR, &wr) != HAL_OK)
  {
    return 0U;
  }
  if (MAX30102_ReadReg(MAX30102_REG_FIFO_RD_PTR, &rd) != HAL_OK)
  {
    return 0U;
  }

  return (uint8_t)((wr - rd) & 0x1FU);
}

static uint8_t MAX30102_ReadSamples(MAX30102_Sample_t *samples, uint8_t max_samples)
{
  uint8_t raw[MAX30102_MAX_FIFO_SAMPLES * 6U];
  uint8_t count = MAX30102_FIFOCount();
  uint8_t i;

  if (count > max_samples)
  {
    count = max_samples;
  }
  if (count > MAX30102_MAX_FIFO_SAMPLES)
  {
    count = MAX30102_MAX_FIFO_SAMPLES;
  }
  if (count == 0U)
  {
    return 0U;
  }

  if (HAL_I2C_Mem_Read(&hi2c3,
                       (uint16_t)(MAX30102_I2C_ADDR_7BIT << 1),
                       MAX30102_REG_FIFO_DATA,
                       I2C_MEMADD_SIZE_8BIT,
                       raw,
                       (uint16_t)(count * 6U),
                       OLED_I2C_TIMEOUT_MS) != HAL_OK)
  {
    return 0U;
  }

  for (i = 0U; i < count; i++)
  {
    uint8_t *p = &raw[i * 6U];

    samples[i].red = (((uint32_t)(p[0] & 0x03U)) << 16) |
                     (((uint32_t)p[1]) << 8) |
                     ((uint32_t)p[2]);
    samples[i].ir = (((uint32_t)(p[3] & 0x03U)) << 16) |
                    (((uint32_t)p[4]) << 8) |
                    ((uint32_t)p[5]);
  }

  return count;
}

static void MAX30102_Service(void)
{
  MAX30102_Sample_t samples[MAX30102_MAX_FIFO_SAMPLES];
  uint32_t start_ms = HAL_GetTick();
  uint32_t service_time_ms;
  uint8_t count;
  uint8_t i;

  if (max30102_initialized == 0U)
  {
    return;
  }

  MAX30102_ClearInterrupts();
  count = MAX30102_ReadSamples(samples, MAX30102_MAX_FIFO_SAMPLES);
#if DIAG_STREAM_USB_ENABLE != 0U
  diag_stats.max30102_service_calls++;
  diag_stats.max30102_samples_read += count;
  Diagnostics_UpdateMax(&diag_stats.max30102_fifo_max, count);
#endif
  service_time_ms = HAL_GetTick();
  for (i = 0U; i < count; i++)
  {
    uint32_t sample_time_ms = service_time_ms;
    uint8_t samples_after = (uint8_t)(count - i - 1U);

    if (service_time_ms >= ((uint32_t)samples_after * MAX30102_SAMPLE_INTERVAL_MS))
    {
      sample_time_ms = service_time_ms - ((uint32_t)samples_after * MAX30102_SAMPLE_INTERVAL_MS);
    }

    max30102_samples_total++;
    max30102_last_red_raw = samples[i].red;
    max30102_last_ir_raw = samples[i].ir;
    PPG_ProcessSample(samples[i].red, samples[i].ir);
    RawStream_WriteSample(sample_time_ms, samples[i].red, samples[i].ir);
  }
#if DIAG_STREAM_USB_ENABLE != 0U
  Diagnostics_UpdateMax(&diag_stats.max30102_service_max_ms, HAL_GetTick() - start_ms);
#endif
}

static void RawStream_WriteHeaderIfNeeded(void)
{
  static const char header[] = "t_ms,red,ir,irq_count,sample_count\r\n";

#if RAW_STREAM_USB_ENABLE == 0U
  (void)header;
  return;
#else
  if ((raw_stream_header_sent == 0U) && (CDC_IsPortOpen_FS() != 0U))
  {
    if (CDC_Write((const uint8_t *)header, (uint16_t)(sizeof(header) - 1U)) == USBD_OK)
    {
      raw_stream_header_sent = 1U;
    }
  }
#endif
}

static void RawStream_WriteSample(uint32_t sample_time_ms, uint32_t red_raw, uint32_t ir_raw)
{
#if RAW_STREAM_USB_ENABLE == 0U
  (void)sample_time_ms;
  (void)red_raw;
  (void)ir_raw;
  return;
#else
  char line[80];
  int len;

  if (CDC_IsPortOpen_FS() == 0U)
  {
    return;
  }

  RawStream_WriteHeaderIfNeeded();
  if (raw_stream_header_sent == 0U)
  {
    return;
  }

  len = snprintf(line,
                 sizeof(line),
                 "%lu,%lu,%lu,%lu,%lu\r\n",
                 (unsigned long)sample_time_ms,
                 (unsigned long)red_raw,
                 (unsigned long)ir_raw,
                 (unsigned long)max30102_irq_count,
                 (unsigned long)max30102_samples_total);
  if (len <= 0)
  {
    return;
  }
  if ((size_t)len >= sizeof(line))
  {
    len = (int)sizeof(line) - 1;
  }

  (void)CDC_Write((uint8_t *)line, (uint16_t)len);
#endif
}

#if 0
static void PPG_Reset(void)
{
  memset(&ppg_state, 0, sizeof(ppg_state));
  finger_present = 0U;
  ppg_signal_valid = 0U;
  PPG_ClearMeasurementState();
  max30102_irq_count = 0U;
  max30102_samples_total = 0U;
  max30102_last_red_raw = 0U;
  max30102_last_ir_raw = 0U;
  raw_stream_header_sent = 0U;
  ppg_proc_header_sent = 0U;
}

static void PPG_ClearMeasurementState(void)
{
  heart_rate_bpm = -1;
  spo2_percent = -1;
  ppg_signal_valid = 0U;
  heart_flash_until_ms = 0U;
  heart_flash_active = 0U;
  ppg_signal_amp = 0U;
  ppg_state.last_beat_ms = 0U;
  ppg_state.last_beat_tick_ms = 0U;
  ppg_state.warmup_count = 0U;
  ppg_state.red_filt = 0.0f;
  ppg_state.ir_filt = 0.0f;
  ppg_state.ir_peak = 0.0f;
  ppg_state.ir_trough = 0.0f;
  ppg_state.prev_ir_filt = 0.0f;
  ppg_state.rr_avg_ms = 0.0f;
  ppg_state.rising = 0U;
  ppg_state.rr_count = 0U;
  ppg_state.red_ac_sq_sum = 0.0f;
  ppg_state.ir_ac_sq_sum = 0.0f;
  ppg_state.red_dc_sum = 0.0f;
  ppg_state.ir_dc_sum = 0.0f;
  ppg_state.spo2_count = 0U;
  ppg_state.valid_beat_count = 0U;
}

static void PPG_ProcessSample(uint32_t red_raw, uint32_t ir_raw)
{
  const float dc_alpha = 0.02f;
  const float filt_alpha = 0.25f;
  const float min_dc = 8000.0f;
  const float min_beat_amp = 50.0f;
  const float finger_on_ir_dc = 30000.0f;
  const float finger_on_red_dc = 22000.0f;
  const float finger_off_ir_dc = 22000.0f;
  const float finger_off_red_dc = 15000.0f;
  const uint16_t warmup_samples = 60U;
  float red_ac;
  float ir_ac;
  float finger_ratio = 0.0f;
  float raw_ratio = 0.0f;
  float slope;
  float amp;
  float peak_gate;
  uint8_t finger_on_candidate;
  uint8_t finger_off_candidate;

  max30102_last_red_raw = red_raw;
  max30102_last_ir_raw = ir_raw;

  if ((red_raw < 1000U) || (ir_raw < 1000U))
  {
    if ((finger_present != 0U) || (heart_rate_bpm > 0) || (spo2_percent > 0))
    {
      last_oled_vitals_update_ms = 0U;
    }
    finger_present = 0U;
    ppg_state.finger_on_count = 0U;
    ppg_state.finger_off_count = FINGER_OFF_CONFIRM_SAMPLES;
    PPG_ClearMeasurementState();
    return;
  }

  if (ir_raw > 0U)
  {
    raw_ratio = (float)red_raw / (float)ir_raw;
  }

  if (ppg_state.warmup_count == 0U)
  {
    ppg_state.red_dc = (float)red_raw;
    ppg_state.ir_dc = (float)ir_raw;
    ppg_state.ir_peak = 0.0f;
    ppg_state.ir_trough = 0.0f;
    ppg_state.sample_time_ms = HAL_GetTick();
  }

  ppg_state.sample_time_ms += MAX30102_SAMPLE_INTERVAL_MS;
  ppg_state.red_dc += dc_alpha * ((float)red_raw - ppg_state.red_dc);
  ppg_state.ir_dc += dc_alpha * ((float)ir_raw - ppg_state.ir_dc);

  if (ppg_state.ir_dc > 1.0f)
  {
    finger_ratio = ppg_state.red_dc / ppg_state.ir_dc;
  }

  finger_on_candidate = ((ir_raw > (uint32_t)finger_on_ir_dc) &&
                         (red_raw > (uint32_t)finger_on_red_dc) &&
                         (ppg_state.ir_dc > finger_on_ir_dc) &&
                         (ppg_state.red_dc > finger_on_red_dc) &&
                         (raw_ratio > 0.45f) &&
                         (raw_ratio < 1.30f) &&
                         (finger_ratio > 0.45f) &&
                         (finger_ratio < 1.30f)) ? 1U : 0U;
  finger_off_candidate = ((ir_raw < (uint32_t)finger_off_ir_dc) ||
                          (red_raw < (uint32_t)finger_off_red_dc) ||
                          (ppg_state.ir_dc < finger_off_ir_dc) ||
                          (ppg_state.red_dc < finger_off_red_dc) ||
                          (raw_ratio < 0.35f) ||
                          (raw_ratio > 1.60f) ||
                          (finger_ratio < 0.35f) ||
                          (finger_ratio > 1.60f)) ? 1U : 0U;

  if (finger_present == 0U)
  {
    if (finger_on_candidate != 0U)
    {
      if (ppg_state.finger_on_count < FINGER_ON_CONFIRM_SAMPLES)
      {
        ppg_state.finger_on_count++;
      }
    }
    else
    {
      ppg_state.finger_on_count = 0U;
    }

    if (ppg_state.finger_on_count >= FINGER_ON_CONFIRM_SAMPLES)
    {
      finger_present = 1U;
      ppg_state.finger_off_count = 0U;
      PPG_ClearMeasurementState();
      last_oled_vitals_update_ms = 0U;
    }
    else
    {
      PPG_ClearMeasurementState();
      return;
    }
  }
  else
  {
    if (finger_off_candidate != 0U)
    {
      if (ppg_state.finger_off_count < FINGER_OFF_CONFIRM_SAMPLES)
      {
        ppg_state.finger_off_count++;
      }
    }
    else
    {
      ppg_state.finger_off_count = 0U;
    }

    if (ppg_state.finger_off_count >= FINGER_OFF_CONFIRM_SAMPLES)
    {
      finger_present = 0U;
      ppg_state.finger_on_count = 0U;
      PPG_ClearMeasurementState();
      last_oled_vitals_update_ms = 0U;
      return;
    }
  }

  red_ac = (float)red_raw - ppg_state.red_dc;
  ir_ac = (float)ir_raw - ppg_state.ir_dc;
  ppg_state.red_filt += filt_alpha * (red_ac - ppg_state.red_filt);
  ppg_state.ir_filt += filt_alpha * (ir_ac - ppg_state.ir_filt);

  if (ppg_state.warmup_count < warmup_samples)
  {
    ppg_state.warmup_count++;
    if ((ppg_state.warmup_count == 1U) || (ppg_state.ir_filt > ppg_state.ir_peak))
    {
      ppg_state.ir_peak = ppg_state.ir_filt;
    }
    if ((ppg_state.warmup_count == 1U) || (ppg_state.ir_filt < ppg_state.ir_trough))
    {
      ppg_state.ir_trough = ppg_state.ir_filt;
    }
    ppg_state.prev_ir_filt = ppg_state.ir_filt;
    if (ppg_state.warmup_count >= warmup_samples)
    {
      ppg_state.ir_peak = ppg_state.ir_filt;
      ppg_state.ir_trough = ppg_state.ir_filt;
      ppg_state.rising = 0U;
    }
    return;
  }

  if (ppg_state.ir_filt > ppg_state.ir_peak)
  {
    ppg_state.ir_peak = ppg_state.ir_filt;
  }
  else
  {
    ppg_state.ir_peak -= (ppg_state.ir_peak - ppg_state.ir_trough) * 0.02f;
  }

  if (ppg_state.ir_filt < ppg_state.ir_trough)
  {
    ppg_state.ir_trough = ppg_state.ir_filt;
  }
  else
  {
    ppg_state.ir_trough += (ppg_state.ir_peak - ppg_state.ir_trough) * 0.02f;
  }

  amp = ppg_state.ir_peak - ppg_state.ir_trough;
  if (amp < 0.0f)
  {
    amp = 0.0f;
  }
  if (amp > 65535.0f)
  {
    ppg_signal_amp = 65535U;
  }
  else
  {
    ppg_signal_amp = (uint16_t)(amp + 0.5f);
  }

  slope = ppg_state.ir_filt - ppg_state.prev_ir_filt;
  peak_gate = ppg_state.ir_trough + (amp * 0.35f);

  if (slope > 0.0f)
  {
    ppg_state.rising = 1U;
  }
  else if ((slope < 0.0f) && (ppg_state.rising != 0U) &&
           (ppg_state.ir_dc > min_dc) && (amp > min_beat_amp) &&
           (ppg_state.prev_ir_filt > peak_gate))
  {
    uint32_t beat_delta = ppg_state.sample_time_ms - ppg_state.last_beat_ms;
    uint8_t record_peak = 0U;
    uint8_t accepted_beat = 0U;

    ppg_state.rising = 0U;
    if (ppg_state.last_beat_ms == 0U)
    {
      record_peak = 1U;
    }
    else if ((beat_delta >= 450U) && (beat_delta <= 1500U))
    {
      float rr_ms = (float)beat_delta;

      if ((ppg_state.rr_count == 0U) ||
          ((rr_ms > (ppg_state.rr_avg_ms * 0.60f)) && (rr_ms < (ppg_state.rr_avg_ms * 1.60f))))
      {
        if (ppg_state.rr_count == 0U)
        {
          ppg_state.rr_avg_ms = rr_ms;
        }
        else
        {
          ppg_state.rr_avg_ms = (0.70f * ppg_state.rr_avg_ms) + (0.30f * rr_ms);
        }

        if (ppg_state.rr_count < 5U)
        {
          ppg_state.rr_count++;
        }
        if (amp >= (float)PPG_VALID_MIN_AMP)
        {
          if (ppg_state.valid_beat_count < PPG_VALID_BEATS_REQUIRED)
          {
            ppg_state.valid_beat_count++;
          }
        }
        else if (ppg_state.valid_beat_count > 0U)
        {
          ppg_state.valid_beat_count--;
        }
        if (ppg_state.valid_beat_count >= PPG_VALID_BEATS_REQUIRED)
        {
          ppg_signal_valid = 1U;
        }
        else
        {
          ppg_signal_valid = 0U;
          spo2_percent = -1;
        }
        heart_rate_bpm = (int16_t)((60000.0f / ppg_state.rr_avg_ms) + 0.5f);
        accepted_beat = 1U;
        record_peak = 1U;
      }
    }
    else if (beat_delta > 1500U)
    {
      ppg_state.rr_count = 0U;
      ppg_state.rr_avg_ms = 0.0f;
      ppg_state.valid_beat_count = 0U;
      ppg_signal_valid = 0U;
      heart_rate_bpm = -1;
      spo2_percent = -1;
      record_peak = 1U;
    }

    if (accepted_beat != 0U)
    {
      heart_flash_until_ms = HAL_GetTick() + HEART_FLASH_MS;
      last_oled_vitals_update_ms = 0U;
    }
    if (record_peak != 0U)
    {
      ppg_state.last_beat_ms = ppg_state.sample_time_ms;
      ppg_state.last_beat_tick_ms = HAL_GetTick();
    }
  }
  ppg_state.prev_ir_filt = ppg_state.ir_filt;

  if ((ppg_state.red_dc > min_dc) && (ppg_state.ir_dc > min_dc) && (amp > min_beat_amp))
  {
    ppg_state.red_ac_sq_sum += red_ac * red_ac;
    ppg_state.ir_ac_sq_sum += ir_ac * ir_ac;
    ppg_state.red_dc_sum += ppg_state.red_dc;
    ppg_state.ir_dc_sum += ppg_state.ir_dc;
    ppg_state.spo2_count++;
  }

  if (ppg_state.spo2_count >= MAX30102_SAMPLE_RATE_HZ)
  {
    float red_rms = sqrtf(ppg_state.red_ac_sq_sum / (float)ppg_state.spo2_count);
    float ir_rms = sqrtf(ppg_state.ir_ac_sq_sum / (float)ppg_state.spo2_count);
    float red_dc_avg = ppg_state.red_dc_sum / (float)ppg_state.spo2_count;
    float ir_dc_avg = ppg_state.ir_dc_sum / (float)ppg_state.spo2_count;

    if ((red_rms > 1.0f) && (ir_rms > 1.0f) && (red_dc_avg > min_dc) && (ir_dc_avg > min_dc))
    {
      float ratio = (red_rms / red_dc_avg) / (ir_rms / ir_dc_avg);

      if ((ratio >= 0.35f) && (ratio <= 1.80f))
      {
        int16_t spo2_calc = (int16_t)((104.0f - (17.0f * ratio)) + 0.5f);

        if (spo2_calc > 100)
        {
          spo2_calc = 100;
        }
        if (spo2_calc < 90)
        {
          spo2_percent = -1;
          ppg_signal_valid = 0U;
          ppg_state.valid_beat_count = 0U;
        }
        else if (ppg_signal_valid == 0U)
        {
          spo2_percent = -1;
        }
        else if (spo2_percent <= 0)
        {
          spo2_percent = spo2_calc;
        }
        else
        {
          spo2_percent = (int16_t)(((int32_t)spo2_percent * 3 + spo2_calc) / 4);
        }
      }
    }

    ppg_state.red_ac_sq_sum = 0.0f;
    ppg_state.ir_ac_sq_sum = 0.0f;
    ppg_state.red_dc_sum = 0.0f;
    ppg_state.ir_dc_sum = 0.0f;
    ppg_state.spo2_count = 0U;
  }
}

static void Vitals_UpdateTimeouts(void)
{
  if (finger_present == 0U)
  {
    if ((heart_rate_bpm > 0) || (spo2_percent > 0) || (ppg_signal_valid != 0U))
    {
      last_oled_vitals_update_ms = 0U;
    }
    heart_rate_bpm = -1;
    spo2_percent = -1;
    ppg_signal_valid = 0U;
    return;
  }

  if (ppg_state.last_beat_tick_ms == 0U)
  {
    if ((heart_rate_bpm > 0) || (spo2_percent > 0) || (ppg_signal_valid != 0U))
    {
      last_oled_vitals_update_ms = 0U;
    }
    heart_rate_bpm = -1;
    spo2_percent = -1;
    ppg_signal_valid = 0U;
    return;
  }

  if ((HAL_GetTick() - ppg_state.last_beat_tick_ms) > 2200U)
  {
    if ((heart_rate_bpm > 0) || (spo2_percent > 0) || (ppg_signal_valid != 0U))
    {
      last_oled_vitals_update_ms = 0U;
    }
    heart_rate_bpm = -1;
    spo2_percent = -1;
    ppg_signal_valid = 0U;
    ppg_state.valid_beat_count = 0U;
  }

  if ((ppg_state.last_beat_tick_ms != 0U) &&
      ((HAL_GetTick() - ppg_state.last_beat_tick_ms) > 5000U))
  {
    heart_rate_bpm = -1;
    spo2_percent = -1;
    ppg_signal_valid = 0U;
    finger_present = 0U;
    ppg_state.rr_count = 0U;
    ppg_state.rr_avg_ms = 0.0f;
    ppg_state.finger_on_count = 0U;
    ppg_state.finger_off_count = FINGER_OFF_CONFIRM_SAMPLES;
    last_oled_vitals_update_ms = 0U;
  }
}

static void Vitals_LogStatus(void)
{
  CDC_Printf("PPG finger=%u valid=%u red=%lu ir=%lu amp=%u hr=%d spo2=%d irq=%lu samples=%lu\r\n",
             (unsigned int)finger_present,
             (unsigned int)ppg_signal_valid,
             (unsigned long)max30102_last_red_raw,
             (unsigned long)max30102_last_ir_raw,
             (unsigned int)ppg_signal_amp,
             (int)heart_rate_bpm,
             (int)spo2_percent,
             (unsigned long)max30102_irq_count,
             (unsigned long)max30102_samples_total);
}
#endif

static void PPG_Reset(void)
{
  memset(&ppg_state, 0, sizeof(ppg_state));
  PPG_AutocorrCancel();
  PPG_ClearMeasurementState();
  max30102_irq_count = 0U;
  max30102_samples_total = 0U;
  max30102_last_red_raw = 0U;
  max30102_last_ir_raw = 0U;
  raw_stream_header_sent = 0U;
}

static void PPG_ClearMeasurementState(void)
{
  memset(&ppg_state, 0, sizeof(ppg_state));
  PPG_RobustPeakReset();
  PPG_AutocorrCancel();
  AF_LiveReset();
  Stress_LiveReset();
  finger_present = 0U;
  heart_rate_bpm = -1;
  hr_autocorr_bpm = -1;
  spo2_percent = -1;
  ppg_signal_valid = 0U;
  heart_flash_until_ms = 0U;
  heart_flash_active = 0U;
  ppg_signal_amp = 0U;
  ppg_finger_score_x100 = 0U;
  ppg_ratio_x1000 = 0U;
}

static void PPG_ClearHrState(void)
{
  AF_LiveReset();
  Stress_LiveReset();
  heart_rate_bpm = -1;
  hr_autocorr_bpm = -1;
  ppg_signal_valid = 0U;
  heart_flash_until_ms = 0U;
  heart_flash_active = 0U;
  ppg_state.peak_count = 0U;
  ppg_state.hr_history_count = 0U;
  ppg_state.hr_history_index = 0U;
  ppg_state.last_hr_update_tick_ms = 0U;
  ppg_state.hr_peak_bpm = 0.0f;
  ppg_state.last_peak_value = 0.0f;
  PPG_RobustPeakReset();
  PPG_AutocorrCancel();
  PPG_ClearHrConfirmState();
}

static void PPG_ClearVitalsState(void)
{
  PPG_ClearHrState();
  spo2_percent = -1;
  ppg_state.ratio = 0.0f;
  ppg_ratio_x1000 = 0U;
}

static void PPG_AppendWindow(float red_filt, float ir_filt)
{
  ppg_state.red_filt_window[ppg_state.window_index] = red_filt;
  ppg_state.ir_filt_window[ppg_state.window_index] = ir_filt;
  ppg_state.window_index++;
  if (ppg_state.window_index >= PPG_WINDOW_SAMPLES)
  {
    ppg_state.window_index = 0U;
  }
  if (ppg_state.window_count < PPG_WINDOW_SAMPLES)
  {
    ppg_state.window_count++;
  }
}

static float PPG_WindowValue(const float *buffer, uint16_t len, uint16_t pos)
{
  uint16_t start = (ppg_state.window_count < PPG_WINDOW_SAMPLES) ? 0U : ppg_state.window_index;
  uint16_t index;

  if ((buffer == NULL) || (len == 0U) || (pos >= len))
  {
    return 0.0f;
  }

  index = (uint16_t)(start + pos);
  if (index >= PPG_WINDOW_SAMPLES)
  {
    index = (uint16_t)(index - PPG_WINDOW_SAMPLES);
  }
  return buffer[index];
}

static float PPG_RmsWindow(const float *buffer, uint16_t len)
{
  float sum = 0.0f;
  uint16_t i;

  if ((buffer == NULL) || (len == 0U))
  {
    return 0.0f;
  }

  for (i = 0U; i < len; i++)
  {
    sum += buffer[i] * buffer[i];
  }

  return sqrtf(sum / (float)len);
}

static uint8_t PPG_DetectPeak(uint32_t sample_time_ms)
{
  float y0;
  float y1;
  float y2;
  float threshold;
  uint8_t i;

  if (ppg_state.window_count < 3U)
  {
    return 0U;
  }

  y0 = PPG_WindowValue(ppg_state.ir_filt_window, ppg_state.window_count, (uint16_t)(ppg_state.window_count - 3U));
  y1 = PPG_WindowValue(ppg_state.ir_filt_window, ppg_state.window_count, (uint16_t)(ppg_state.window_count - 2U));
  y2 = PPG_WindowValue(ppg_state.ir_filt_window, ppg_state.window_count, (uint16_t)(ppg_state.window_count - 1U));
  threshold = (PPG_MIN_AC_RMS > (0.45f * ppg_state.ir_rms)) ? PPG_MIN_AC_RMS : (0.45f * ppg_state.ir_rms);

  if (!((y1 > y0) && (y1 >= y2) && (y1 > threshold)))
  {
    return 0U;
  }

  if (ppg_state.peak_count > 0U)
  {
    uint32_t dt_ms = sample_time_ms - ppg_state.peak_ms[ppg_state.peak_count - 1U];
    if (dt_ms < PPG_MIN_PEAK_MS)
    {
      return 0U;
    }
  }

  ppg_state.last_peak_value = y1;

  if (ppg_state.peak_count < PPG_PEAK_HISTORY_SIZE)
  {
    ppg_state.peak_ms[ppg_state.peak_count++] = sample_time_ms;
  }
  else
  {
    for (i = 1U; i < PPG_PEAK_HISTORY_SIZE; i++)
    {
      ppg_state.peak_ms[i - 1U] = ppg_state.peak_ms[i];
    }
    ppg_state.peak_ms[PPG_PEAK_HISTORY_SIZE - 1U] = sample_time_ms;
  }

  if (ppg_state.peak_count >= 4U)
  {
    float intervals[5];
    uint8_t interval_count = 0U;
    uint8_t start_index = 1U;

    if (ppg_state.peak_count > 6U)
    {
      start_index = (uint8_t)(ppg_state.peak_count - 5U);
    }

    for (i = start_index; i < ppg_state.peak_count; i++)
    {
      uint32_t dt_ms = ppg_state.peak_ms[i] - ppg_state.peak_ms[i - 1U];

      if ((dt_ms >= PPG_MIN_PEAK_MS) && (dt_ms <= PPG_MAX_PEAK_MS))
      {
        intervals[interval_count++] = (float)dt_ms;
      }
    }

    if (interval_count >= 3U)
    {
      float rr_ms = PPG_MedianFloat(intervals, interval_count);
      float hr_bpm_value = 60000.0f / rr_ms;

      if ((hr_bpm_value >= PPG_MIN_VALID_HR_BPM) &&
          (hr_bpm_value <= PPG_MAX_VALID_HR_BPM))
      {
        ppg_state.hr_peak_bpm = hr_bpm_value;
      }
      else
      {
        ppg_state.hr_peak_bpm = 0.0f;
      }
    }
  }

  return 1U;
}

static void PPG_RobustPeakReset(void)
{
  memset(&ppg_robust_peak, 0, sizeof(ppg_robust_peak));
}

static uint8_t PPG_DetectRobustPeak(uint32_t sample_time_ms, uint32_t *peak_time_ms)
{
  uint32_t candidate_time_ms;
  uint8_t accepted;
  float y0;
  float y1;
  float y2;
  float threshold;

  if (peak_time_ms != NULL)
  {
    *peak_time_ms = 0U;
  }

  if (ppg_state.window_count < 3U)
  {
    return 0U;
  }

  accepted = PPG_RobustFlushPendingPeak(sample_time_ms, peak_time_ms);

  y0 = PPG_WindowValue(ppg_state.ir_filt_window, ppg_state.window_count, (uint16_t)(ppg_state.window_count - 3U));
  y1 = PPG_WindowValue(ppg_state.ir_filt_window, ppg_state.window_count, (uint16_t)(ppg_state.window_count - 2U));
  y2 = PPG_WindowValue(ppg_state.ir_filt_window, ppg_state.window_count, (uint16_t)(ppg_state.window_count - 1U));
  candidate_time_ms = (sample_time_ms >= MAX30102_SAMPLE_INTERVAL_MS) ?
                      (sample_time_ms - MAX30102_SAMPLE_INTERVAL_MS) :
                      sample_time_ms;
  threshold = PPG_RobustPeakThreshold(candidate_time_ms);

  if (!((y1 > y0) && (y1 >= y2) && (y1 > threshold)))
  {
    return accepted;
  }

  if (ppg_robust_peak.peak_count > 0U)
  {
    uint32_t dt_ms = candidate_time_ms -
                     ppg_robust_peak.peak_ms[ppg_robust_peak.peak_count - 1U];
    if (dt_ms < PPG_RobustDynamicMinPeakMs())
    {
      return accepted;
    }
  }

  PPG_RobustStorePendingPeak(candidate_time_ms, y1);
  return accepted;
}

static uint8_t PPG_RobustFlushPendingPeak(uint32_t sample_time_ms, uint32_t *peak_time_ms)
{
  if (ppg_robust_peak.pending_peak_valid == 0U)
  {
    return 0U;
  }

  if ((sample_time_ms - ppg_robust_peak.pending_peak_ms) < PPG_ROBUST_CANDIDATE_HOLD_MS)
  {
    return 0U;
  }

  if (peak_time_ms != NULL)
  {
    *peak_time_ms = ppg_robust_peak.pending_peak_ms;
  }
  PPG_RobustAppendPeak(ppg_robust_peak.pending_peak_ms,
                       ppg_robust_peak.pending_peak_value);
  ppg_robust_peak.pending_peak_valid = 0U;
  ppg_robust_peak.pending_peak_ms = 0U;
  ppg_robust_peak.pending_peak_value = 0.0f;
  return 1U;
}

static void PPG_RobustStorePendingPeak(uint32_t peak_time_ms, float peak_value)
{
  if (ppg_robust_peak.pending_peak_valid == 0U)
  {
    ppg_robust_peak.pending_peak_valid = 1U;
    ppg_robust_peak.pending_peak_ms = peak_time_ms;
    ppg_robust_peak.pending_peak_value = peak_value;
    return;
  }

  if ((peak_time_ms - ppg_robust_peak.pending_peak_ms) <= PPG_ROBUST_CANDIDATE_HOLD_MS)
  {
    if (peak_value > ppg_robust_peak.pending_peak_value)
    {
      ppg_robust_peak.pending_peak_ms = peak_time_ms;
      ppg_robust_peak.pending_peak_value = peak_value;
    }
  }
  else
  {
    ppg_robust_peak.pending_peak_ms = peak_time_ms;
    ppg_robust_peak.pending_peak_value = peak_value;
  }
}

static void PPG_RobustAppendPeak(uint32_t peak_time_ms, float peak_value)
{
  uint8_t i;

  ppg_robust_peak.last_peak_value = peak_value;

  if (ppg_robust_peak.peak_count < PPG_PEAK_HISTORY_SIZE)
  {
    ppg_robust_peak.peak_ms[ppg_robust_peak.peak_count++] = peak_time_ms;
  }
  else
  {
    for (i = 1U; i < PPG_PEAK_HISTORY_SIZE; i++)
    {
      ppg_robust_peak.peak_ms[i - 1U] = ppg_robust_peak.peak_ms[i];
    }
    ppg_robust_peak.peak_ms[PPG_PEAK_HISTORY_SIZE - 1U] = peak_time_ms;
  }
}

static float PPG_RobustReferencePpiMs(void)
{
  if ((heart_rate_bpm >= (int16_t)PPG_MIN_VALID_HR_BPM) &&
      (heart_rate_bpm <= (int16_t)PPG_MAX_VALID_HR_BPM))
  {
    return 60000.0f / (float)heart_rate_bpm;
  }

  if ((hr_autocorr_bpm >= (int16_t)PPG_MIN_VALID_HR_BPM) &&
      (hr_autocorr_bpm <= (int16_t)PPG_MAX_VALID_HR_BPM))
  {
    return 60000.0f / (float)hr_autocorr_bpm;
  }

  return 0.0f;
}

static uint32_t PPG_RobustDynamicMinPeakMs(void)
{
  float reference_ppi_ms = PPG_RobustReferencePpiMs();
  float dynamic_min_ms = PPG_MIN_PEAK_MS;

  if (reference_ppi_ms > 0.0f)
  {
    dynamic_min_ms = reference_ppi_ms * PPG_ROBUST_DYNAMIC_MIN_HR_RATIO;
    if (dynamic_min_ms < (float)PPG_MIN_PEAK_MS)
    {
      dynamic_min_ms = (float)PPG_MIN_PEAK_MS;
    }
  }

  return (uint32_t)(dynamic_min_ms + 0.5f);
}

static float PPG_RobustPeakThreshold(uint32_t peak_time_ms)
{
  float threshold_ratio = PPG_ROBUST_BASE_THRESHOLD_RATIO;
  float reference_ppi_ms = PPG_RobustReferencePpiMs();

  if ((reference_ppi_ms > 0.0f) && (ppg_robust_peak.peak_count > 0U))
  {
    uint32_t dt_ms = peak_time_ms -
                     ppg_robust_peak.peak_ms[ppg_robust_peak.peak_count - 1U];
    if ((float)dt_ms >= (reference_ppi_ms * PPG_ROBUST_RECOVERY_START_HR_RATIO))
    {
      threshold_ratio = PPG_ROBUST_RECOVERY_THRESHOLD_RATIO;
    }
  }

  if (PPG_MIN_AC_RMS > (threshold_ratio * ppg_state.ir_rms))
  {
    return PPG_MIN_AC_RMS;
  }
  return threshold_ratio * ppg_state.ir_rms;
}

static uint8_t PPG_HrvPpiIsUsable(uint32_t interval_ms)
{
  float bpm;
  float expected_ppi_ms;
  float min_ratio;
  float max_ratio;

  if ((interval_ms < 300U) || (interval_ms > 2200U))
  {
    return 0U;
  }

  bpm = PPG_HrvCurrentBpm();
  if (bpm <= 0.0f)
  {
    return 1U;
  }

  expected_ppi_ms = 60000.0f / bpm;
  PPG_HrvPpiRatioLimits(bpm, &min_ratio, &max_ratio);
  if (((float)interval_ms < (expected_ppi_ms * min_ratio)) ||
      ((float)interval_ms > (expected_ppi_ms * max_ratio)))
  {
    return 0U;
  }

  return 1U;
}

static float PPG_HrvCurrentBpm(void)
{
  if ((heart_rate_bpm >= (int16_t)PPG_MIN_VALID_HR_BPM) &&
      (heart_rate_bpm <= (int16_t)PPG_MAX_VALID_HR_BPM))
  {
    return (float)heart_rate_bpm;
  }

  if ((hr_autocorr_bpm >= (int16_t)PPG_MIN_VALID_HR_BPM) &&
      (hr_autocorr_bpm <= (int16_t)PPG_MAX_VALID_HR_BPM))
  {
    return (float)hr_autocorr_bpm;
  }

  return 0.0f;
}

static void PPG_HrvPpiRatioLimits(float bpm, float *min_ratio, float *max_ratio)
{
  float min_value = PPG_PPI_FILTER_MIN_HR_RATIO;
  float max_value = PPG_PPI_FILTER_MAX_HR_RATIO;

  if (bpm < 65.0f)
  {
    min_value = PPG_PPI_FILTER_SLOW_MIN_RATIO;
    max_value = PPG_PPI_FILTER_SLOW_MAX_RATIO;
  }
  else if (bpm > 115.0f)
  {
    min_value = PPG_PPI_FILTER_FAST_MIN_RATIO;
    max_value = PPG_PPI_FILTER_FAST_MAX_RATIO;
  }

  if (min_ratio != NULL)
  {
    *min_ratio = min_value;
  }
  if (max_ratio != NULL)
  {
    *max_ratio = max_value;
  }
}

static uint8_t PPG_HrvWindowQualityOk(uint32_t accepted_count, uint32_t rejected_count)
{
  uint32_t total_count = accepted_count + rejected_count;

  if (total_count < PPG_PPI_QUALITY_MIN_INTERVALS)
  {
    return 1U;
  }

  if ((rejected_count * 100U) > (total_count * PPG_PPI_QUALITY_MAX_REJECT_PCT))
  {
    return 0U;
  }

  return 1U;
}

static float PPG_EstimateHrAutocorr(void)
{
  uint16_t len = ppg_state.window_count;
  uint16_t i;
  uint16_t lag;
  uint16_t min_lag = (uint16_t)(PPG_MIN_PEAK_MS / MAX30102_SAMPLE_INTERVAL_MS);
  uint16_t max_lag = (uint16_t)(PPG_MAX_PEAK_MS / MAX30102_SAMPLE_INTERVAL_MS);
  float mean = 0.0f;
  float energy = 0.0f;
  float best_corr = -1.0f;
  uint16_t best_lag = 0U;

  if (len < PPG_AUTOCORR_MIN_SAMPLES)
  {
    return 0.0f;
  }
  if (max_lag > (uint16_t)(len - 2U))
  {
    max_lag = (uint16_t)(len - 2U);
  }
  if (min_lag < 1U)
  {
    min_lag = 1U;
  }
  if (min_lag > max_lag)
  {
    return 0.0f;
  }

  for (i = 0U; i < len; i++)
  {
    mean += PPG_WindowValue(ppg_state.ir_filt_window, len, i);
  }
  mean /= (float)len;

  for (i = 0U; i < len; i++)
  {
    float centered = PPG_WindowValue(ppg_state.ir_filt_window, len, i) - mean;
    energy += centered * centered;
  }
  if (energy <= 1.0f)
  {
    return 0.0f;
  }

  for (lag = min_lag; lag <= max_lag; lag++)
  {
    float corr = 0.0f;

    for (i = lag; i < len; i++)
    {
      float a = PPG_WindowValue(ppg_state.ir_filt_window, len, i) - mean;
      float b = PPG_WindowValue(ppg_state.ir_filt_window, len, (uint16_t)(i - lag)) - mean;
      corr += a * b;
    }
    corr /= energy;

    if (corr > best_corr)
    {
      best_corr = corr;
      best_lag = lag;
    }
  }

  if ((best_lag == 0U) || (best_corr < PPG_AUTOCORR_MIN_CORR))
  {
    return 0.0f;
  }

  {
    float hr_bpm_value = 6000.0f / (float)best_lag;

    if ((hr_bpm_value < PPG_MIN_VALID_HR_BPM) ||
        (hr_bpm_value > PPG_MAX_VALID_HR_BPM))
    {
      return 0.0f;
    }

    return hr_bpm_value;
  }
}

static void PPG_AutocorrStart(uint32_t sample_count)
{
  uint16_t len = ppg_state.window_count;
  uint16_t i;
  uint16_t min_lag = (uint16_t)(PPG_MIN_PEAK_MS / MAX30102_SAMPLE_INTERVAL_MS);
  uint16_t max_lag = (uint16_t)(PPG_MAX_PEAK_MS / MAX30102_SAMPLE_INTERVAL_MS);

  if (ppg_autocorr_job.phase != PPG_AUTOCORR_JOB_IDLE)
  {
    return;
  }

  if (len < PPG_AUTOCORR_MIN_SAMPLES)
  {
    PPG_HandleAutocorrResult(0.0f, sample_count);
    return;
  }
  if (max_lag > (uint16_t)(len - 2U))
  {
    max_lag = (uint16_t)(len - 2U);
  }
  if (min_lag < 1U)
  {
    min_lag = 1U;
  }
  if (min_lag > max_lag)
  {
    PPG_HandleAutocorrResult(0.0f, sample_count);
    return;
  }

  memset(&ppg_autocorr_job, 0, sizeof(ppg_autocorr_job));
  ppg_autocorr_job.len = len;
  ppg_autocorr_job.min_lag = min_lag;
  ppg_autocorr_job.max_lag = max_lag;
  ppg_autocorr_job.lag = min_lag;
  ppg_autocorr_job.i = 0U;
  ppg_autocorr_job.best_corr = -1.0f;
  ppg_autocorr_job.sample_count = sample_count;
  ppg_autocorr_job.phase = PPG_AUTOCORR_JOB_MEAN;

  for (i = 0U; i < len; i++)
  {
    ppg_autocorr_job.values[i] = PPG_WindowValue(ppg_state.ir_filt_window, len, i);
  }
}

static void PPG_AutocorrService(void)
{
  uint32_t budget = PPG_AUTOCORR_SERVICE_BUDGET;
  uint32_t start_ms;

  if (ppg_autocorr_job.phase == PPG_AUTOCORR_JOB_IDLE)
  {
    return;
  }

  start_ms = HAL_GetTick();
#if DIAG_STREAM_USB_ENABLE != 0U
  diag_stats.autocorr_service_calls++;
#endif

  while ((budget > 0U) && (ppg_autocorr_job.phase != PPG_AUTOCORR_JOB_IDLE))
  {
    if (ppg_autocorr_job.phase == PPG_AUTOCORR_JOB_MEAN)
    {
      if (ppg_autocorr_job.i < ppg_autocorr_job.len)
      {
        ppg_autocorr_job.mean += ppg_autocorr_job.values[ppg_autocorr_job.i];
        ppg_autocorr_job.i++;
        budget--;
      }
      else
      {
        ppg_autocorr_job.mean /= (float)ppg_autocorr_job.len;
        ppg_autocorr_job.i = 0U;
        ppg_autocorr_job.phase = PPG_AUTOCORR_JOB_ENERGY;
      }
    }
    else if (ppg_autocorr_job.phase == PPG_AUTOCORR_JOB_ENERGY)
    {
      if (ppg_autocorr_job.i < ppg_autocorr_job.len)
      {
        float centered = ppg_autocorr_job.values[ppg_autocorr_job.i] - ppg_autocorr_job.mean;

        ppg_autocorr_job.energy += centered * centered;
        ppg_autocorr_job.i++;
        budget--;
      }
      else
      {
        if (ppg_autocorr_job.energy <= 1.0f)
        {
          PPG_HandleAutocorrResult(0.0f, ppg_autocorr_job.sample_count);
          ppg_autocorr_job.phase = PPG_AUTOCORR_JOB_IDLE;
        }
        else
        {
          ppg_autocorr_job.i = ppg_autocorr_job.lag;
          ppg_autocorr_job.corr = 0.0f;
          ppg_autocorr_job.phase = PPG_AUTOCORR_JOB_LAG;
        }
      }
    }
    else if (ppg_autocorr_job.phase == PPG_AUTOCORR_JOB_LAG)
    {
      if (ppg_autocorr_job.lag > ppg_autocorr_job.max_lag)
      {
        float hr_bpm_value = 0.0f;

        if ((ppg_autocorr_job.best_lag != 0U) &&
            (ppg_autocorr_job.best_corr >= PPG_AUTOCORR_MIN_CORR))
        {
          hr_bpm_value = 6000.0f / (float)ppg_autocorr_job.best_lag;
          if ((hr_bpm_value < PPG_MIN_VALID_HR_BPM) ||
              (hr_bpm_value > PPG_MAX_VALID_HR_BPM))
          {
            hr_bpm_value = 0.0f;
          }
        }

        PPG_HandleAutocorrResult(hr_bpm_value, ppg_autocorr_job.sample_count);
#if DIAG_STREAM_USB_ENABLE != 0U
        diag_stats.autocorr_jobs_done++;
#endif
        ppg_autocorr_job.phase = PPG_AUTOCORR_JOB_IDLE;
      }
      else if (ppg_autocorr_job.i < ppg_autocorr_job.len)
      {
        float a = ppg_autocorr_job.values[ppg_autocorr_job.i] - ppg_autocorr_job.mean;
        float b = ppg_autocorr_job.values[ppg_autocorr_job.i - ppg_autocorr_job.lag] -
                  ppg_autocorr_job.mean;

        ppg_autocorr_job.corr += a * b;
        ppg_autocorr_job.i++;
        budget--;
      }
      else
      {
        float corr = ppg_autocorr_job.corr / ppg_autocorr_job.energy;

        if (corr > ppg_autocorr_job.best_corr)
        {
          ppg_autocorr_job.best_corr = corr;
          ppg_autocorr_job.best_lag = ppg_autocorr_job.lag;
        }
        ppg_autocorr_job.lag++;
        ppg_autocorr_job.i = ppg_autocorr_job.lag;
        ppg_autocorr_job.corr = 0.0f;
        budget--;
      }
    }
    else
    {
      ppg_autocorr_job.phase = PPG_AUTOCORR_JOB_IDLE;
    }
  }

#if DIAG_STREAM_USB_ENABLE != 0U
  Diagnostics_UpdateMax(&diag_stats.autocorr_service_max_ms, HAL_GetTick() - start_ms);
#endif
}

static void PPG_AutocorrCancel(void)
{
  memset(&ppg_autocorr_job, 0, sizeof(ppg_autocorr_job));
  ppg_autocorr_job.phase = PPG_AUTOCORR_JOB_IDLE;
}

static void PPG_HandleAutocorrResult(float hr, uint32_t sample_count)
{
  if (hr > 0.0f)
  {
    float hr_to_store = 0.0f;
    float hist[PPG_HR_HISTORY_SIZE];
    uint8_t i;

    hr_autocorr_bpm = PPG_RoundToInt16(hr);
    if (PPG_UpdateHrConfirmation(hr, sample_count, &hr_to_store) != 0U)
    {
      int16_t next_hr_bpm = PPG_RoundToInt16(hr_to_store);

      if (PPG_ShouldAcceptHrDisplay(next_hr_bpm) != 0U)
      {
        PPG_AppendHrHistory(hr_to_store);
        for (i = 0U; i < ppg_state.hr_history_count; i++)
        {
          hist[i] = ppg_state.hr_history[i];
        }
        heart_rate_bpm = PPG_RoundToInt16(PPG_MedianFloat(hist, ppg_state.hr_history_count));
        ppg_state.last_hr_update_tick_ms = HAL_GetTick();
      }

      ppg_signal_valid = 1U;
      last_oled_vitals_update_ms = 0U;
    }
    else
    {
      heart_rate_bpm = -1;
      ppg_signal_valid = 0U;
    }
  }
  else if ((ppg_state.hr_confirmed != 0U) && (ppg_state.hr_history_count > 0U))
  {
    float hist[PPG_HR_HISTORY_SIZE];
    uint8_t i;

    for (i = 0U; i < ppg_state.hr_history_count; i++)
    {
      hist[i] = ppg_state.hr_history[i];
    }
    heart_rate_bpm = PPG_RoundToInt16(PPG_MedianFloat(hist, ppg_state.hr_history_count));
    ppg_signal_valid = 1U;
  }
}

static uint8_t PPG_ShouldAcceptHrDisplay(int16_t next_hr_bpm)
{
  int16_t diff;

  if ((next_hr_bpm < (int16_t)PPG_MIN_VALID_HR_BPM) ||
      (next_hr_bpm > (int16_t)PPG_MAX_VALID_HR_BPM))
  {
    return 0U;
  }

  if ((heart_rate_bpm <= 0) ||
      (heart_rate_bpm < (int16_t)PPG_MIN_VALID_HR_BPM) ||
      (heart_rate_bpm > (int16_t)PPG_MAX_VALID_HR_BPM) ||
      (ppg_state.last_hr_update_tick_ms == 0U))
  {
    return 1U;
  }

  diff = (next_hr_bpm > heart_rate_bpm) ?
         (int16_t)(next_hr_bpm - heart_rate_bpm) :
         (int16_t)(heart_rate_bpm - next_hr_bpm);
  if (diff < PPG_HR_MAX_DISPLAY_JUMP_BPM)
  {
    return 1U;
  }

  if ((HAL_GetTick() - ppg_state.last_hr_update_tick_ms) >= PPG_HR_JUMP_ACCEPT_TIMEOUT_MS)
  {
    return 1U;
  }

  return 0U;
}

static void PPG_AppendHrHistory(float hr_bpm_value)
{
  ppg_state.hr_history[ppg_state.hr_history_index] = hr_bpm_value;
  ppg_state.hr_history_index++;
  if (ppg_state.hr_history_index >= PPG_HR_HISTORY_SIZE)
  {
    ppg_state.hr_history_index = 0U;
  }
  if (ppg_state.hr_history_count < PPG_HR_HISTORY_SIZE)
  {
    ppg_state.hr_history_count++;
  }
}

static float PPG_MedianFloat(float *values, uint8_t count)
{
  uint8_t i;

  if ((values == NULL) || (count == 0U))
  {
    return 0.0f;
  }

  for (i = 1U; i < count; i++)
  {
    float key = values[i];
    int8_t j = (int8_t)i - 1;

    while ((j >= 0) && (values[j] > key))
    {
      values[j + 1] = values[j];
      j--;
    }
    values[j + 1] = key;
  }

  if ((count & 0x01U) != 0U)
  {
    return values[count / 2U];
  }

  return (values[(count / 2U) - 1U] + values[count / 2U]) * 0.5f;
}

static int16_t PPG_RoundToInt16(float value)
{
  if (value >= 0.0f)
  {
    return (int16_t)(value + 0.5f);
  }
  return (int16_t)(value - 0.5f);
}

static int32_t PPG_RoundToInt32(float value)
{
  if (value >= 0.0f)
  {
    return (int32_t)(value + 0.5f);
  }
  return (int32_t)(value - 0.5f);
}

static void PPG_ClearHrConfirmState(void)
{
  uint8_t i;

  for (i = 0U; i < PPG_HR_CONFIRM_SAMPLES; i++)
  {
    ppg_state.hr_confirm_candidates[i] = 0.0f;
  }
  ppg_state.hr_confirm_count = 0U;
  ppg_state.hr_confirm_index = 0U;
  ppg_state.hr_confirmed = 0U;
  ppg_state.last_hr_confirm_sample = 0U;
}

static uint8_t PPG_UpdateHrConfirmation(float hr_bpm_value, uint32_t sample_count, float *confirmed_hr)
{
  float min_hr;
  float max_hr;
  uint8_t count;
  uint8_t i;

  if (confirmed_hr == NULL)
  {
    return 0U;
  }
  *confirmed_hr = 0.0f;

  if ((hr_bpm_value < PPG_MIN_VALID_HR_BPM) ||
      (hr_bpm_value > PPG_MAX_VALID_HR_BPM))
  {
    return 0U;
  }

  if (ppg_state.hr_confirmed != 0U)
  {
    *confirmed_hr = hr_bpm_value;
    return 1U;
  }

  if ((ppg_state.last_hr_confirm_sample != 0U) &&
      ((sample_count - ppg_state.last_hr_confirm_sample) < PPG_HR_CONFIRM_INTERVAL_SAMPLES))
  {
    return 0U;
  }

  ppg_state.hr_confirm_candidates[ppg_state.hr_confirm_index] = hr_bpm_value;
  ppg_state.hr_confirm_index++;
  if (ppg_state.hr_confirm_index >= PPG_HR_CONFIRM_SAMPLES)
  {
    ppg_state.hr_confirm_index = 0U;
  }
  if (ppg_state.hr_confirm_count < PPG_HR_CONFIRM_SAMPLES)
  {
    ppg_state.hr_confirm_count++;
  }
  ppg_state.last_hr_confirm_sample = sample_count;

  count = ppg_state.hr_confirm_count;
  if (count < PPG_HR_CONFIRM_SAMPLES)
  {
    return 0U;
  }

  min_hr = ppg_state.hr_confirm_candidates[0];
  max_hr = ppg_state.hr_confirm_candidates[0];
  for (i = 1U; i < count; i++)
  {
    if (ppg_state.hr_confirm_candidates[i] < min_hr)
    {
      min_hr = ppg_state.hr_confirm_candidates[i];
    }
    if (ppg_state.hr_confirm_candidates[i] > max_hr)
    {
      max_hr = ppg_state.hr_confirm_candidates[i];
    }
  }

  if ((max_hr - min_hr) <= PPG_HR_CONFIRM_TOLERANCE_BPM)
  {
    float candidates[PPG_HR_CONFIRM_SAMPLES];

    for (i = 0U; i < count; i++)
    {
      candidates[i] = ppg_state.hr_confirm_candidates[i];
    }
    *confirmed_hr = PPG_MedianFloat(candidates, count);
    ppg_state.hr_confirmed = 1U;
    return 1U;
  }

  return 0U;
}

static void PPG_ProcessSample(uint32_t red_raw, uint32_t ir_raw)
{
  uint32_t sample_count = max30102_samples_total;
  uint32_t sample_time_ms = sample_count * MAX30102_SAMPLE_INTERVAL_MS;
  uint32_t samples_since_finger;
  float dc_score;
  float ac_score;
  uint8_t detected_finger_present;
  uint8_t robust_peak_detected = 0U;
  uint32_t robust_peak_time_ms = 0U;

  max30102_last_red_raw = red_raw;
  max30102_last_ir_raw = ir_raw;

  if (ir_raw < (uint32_t)PPG_FINGER_IR_MIN)
  {
    if ((finger_present != 0U) || (heart_rate_bpm > 0) || (spo2_percent > 0))
    {
      last_oled_vitals_update_ms = 0U;
    }
    PPG_ClearMeasurementState();
    OLED091_WaveformUpdate(sample_count, 0U);
    return;
  }

  if (ppg_state.raw_finger_present == 0U)
  {
    PPG_ClearMeasurementState();
    ppg_state.raw_finger_present = 1U;
    ppg_state.finger_start_sample = sample_count;
    ppg_state.last_autocorr_sample = sample_count;
    ppg_state.red_dc = (float)red_raw;
    ppg_state.ir_dc = (float)ir_raw;
    finger_present = 0U;
    last_oled_vitals_update_ms = 0U;
  }

  ppg_state.red_dc += PPG_DC_ALPHA * ((float)red_raw - ppg_state.red_dc);
  ppg_state.ir_dc += PPG_DC_ALPHA * ((float)ir_raw - ppg_state.ir_dc);
  ppg_state.red_ac = (float)red_raw - ppg_state.red_dc;
  ppg_state.ir_ac = (float)ir_raw - ppg_state.ir_dc;
  ppg_state.red_filt += PPG_FILT_ALPHA * (ppg_state.red_ac - ppg_state.red_filt);
  ppg_state.ir_filt += PPG_FILT_ALPHA * (ppg_state.ir_ac - ppg_state.ir_filt);
  PPG_AppendWindow(ppg_state.red_filt, ppg_state.ir_filt);

  ppg_state.red_rms = PPG_RmsWindow(ppg_state.red_filt_window, ppg_state.window_count);
  ppg_state.ir_rms = PPG_RmsWindow(ppg_state.ir_filt_window, ppg_state.window_count);
  dc_score = (PPG_FINGER_IR_MIN > 0.0f) ? (ppg_state.ir_dc / PPG_FINGER_IR_MIN) : 0.0f;
  ac_score = (PPG_MIN_AC_RMS > 0.0f) ? (ppg_state.ir_rms / PPG_MIN_AC_RMS) : 0.0f;
  ppg_state.finger_score = (dc_score < ac_score) ? dc_score : ac_score;
  ppg_finger_score_x100 = (uint16_t)((ppg_state.finger_score * 100.0f) + 0.5f);
  ppg_signal_amp = (ppg_state.ir_rms > 65535.0f) ? 65535U : (uint16_t)(ppg_state.ir_rms + 0.5f);

  detected_finger_present = ((ppg_state.ir_dc >= PPG_FINGER_IR_MIN) &&
                             (ppg_state.ir_rms >= PPG_MIN_AC_RMS)) ? 1U : 0U;
  if (detected_finger_present == 0U)
  {
    if ((finger_present != 0U) || (heart_rate_bpm > 0) || (spo2_percent > 0))
    {
      last_oled_vitals_update_ms = 0U;
    }
    finger_present = 0U;
    PPG_ClearVitalsState();
    OLED091_WaveformUpdate(sample_count, 0U);
    return;
  }

  if (finger_present == 0U)
  {
    finger_present = 1U;
    PPG_ClearVitalsState();
    ppg_state.finger_start_sample = sample_count;
    ppg_state.last_autocorr_sample = sample_count;
    last_oled_vitals_update_ms = 0U;
    OLED091_WaveformUpdate(sample_count, 0U);
    return;
  }

  samples_since_finger = sample_count - ppg_state.finger_start_sample;
  OLED091_WaveformUpdate(sample_count, 0U);

  if (samples_since_finger < PPG_HR_WARMUP_SAMPLES)
  {
    heart_rate_bpm = -1;
    hr_autocorr_bpm = -1;
    ppg_signal_valid = 0U;
    PPG_ClearHrConfirmState();
    spo2_percent = -1;
    ppg_state.ratio = 0.0f;
    ppg_ratio_x1000 = 0U;
    return;
  }

  if ((sample_count - ppg_state.last_autocorr_sample) >= PPG_AUTOCORR_INTERVAL_SAMPLES)
  {
    if (ppg_autocorr_job.phase == PPG_AUTOCORR_JOB_IDLE)
    {
      ppg_state.last_autocorr_sample = sample_count;
      PPG_AutocorrStart(sample_count);
    }
  }

  if ((finger_present != 0U) &&
      (ppg_signal_valid != 0U) &&
      (heart_rate_bpm > 0))
  {
    robust_peak_detected = PPG_DetectRobustPeak(sample_time_ms, &robust_peak_time_ms);
    if (robust_peak_detected != 0U)
    {
      OLED091_WaveformUpdate(sample_count, robust_peak_detected);
    }
  }

  if (samples_since_finger < PPG_SPO2_WARMUP_SAMPLES)
  {
    spo2_percent = -1;
    ppg_state.ratio = 0.0f;
    ppg_ratio_x1000 = 0U;
    return;
  }

  if ((ppg_state.red_dc > 0.0f) &&
      (ppg_state.ir_dc > 0.0f) &&
      (ppg_state.red_rms > 1.0f) &&
      (ppg_state.ir_rms > 1.0f))
  {
    ppg_state.ratio = (ppg_state.red_rms / ppg_state.red_dc) / (ppg_state.ir_rms / ppg_state.ir_dc);
    ppg_ratio_x1000 = (uint16_t)((ppg_state.ratio * 1000.0f) + 0.5f);

    if ((ppg_state.finger_score >= PPG_MIN_SPO2_SCORE) &&
        (ppg_state.ratio >= 0.40f) &&
        (ppg_state.ratio <= 0.85f))
    {
      float spo2 = 101.0f - (7.0f * ppg_state.ratio);

      if (spo2 > 100.0f)
      {
        spo2 = 100.0f;
      }
      if (spo2 < 70.0f)
      {
        spo2 = 70.0f;
      }
      spo2_percent = PPG_RoundToInt16(spo2);
    }
    else
    {
      spo2_percent = -1;
    }
  }
  else
  {
    ppg_state.ratio = 0.0f;
    ppg_ratio_x1000 = 0U;
    spo2_percent = -1;
  }

  if ((robust_peak_detected != 0U) &&
      (finger_present != 0U) &&
      (ppg_signal_valid != 0U) &&
      (heart_rate_bpm > 0) &&
      (spo2_percent > 0))
  {
    AF_LiveRecordPeak(robust_peak_time_ms);
    Stress_LiveRecordPeak(robust_peak_time_ms);
  }
  AF_LiveUpdateRisk();
  Stress_LiveUpdateIndex();
}

static void Vitals_UpdateTimeouts(void)
{
  if (finger_present == 0U)
  {
    heart_rate_bpm = -1;
    hr_autocorr_bpm = -1;
    spo2_percent = -1;
    ppg_signal_valid = 0U;
    return;
  }

  if ((ppg_state.last_hr_update_tick_ms != 0U) &&
      ((HAL_GetTick() - ppg_state.last_hr_update_tick_ms) > PPG_HR_STALE_TIMEOUT_MS))
  {
    uint32_t hr_age_ms = HAL_GetTick() - ppg_state.last_hr_update_tick_ms;
    uint8_t signal_still_good =
        ((ppg_state.raw_finger_present != 0U) &&
         (ppg_state.finger_score >= PPG_MIN_SPO2_SCORE) &&
         (ppg_state.ir_rms >= PPG_MIN_AC_RMS)) ? 1U : 0U;

    if ((signal_still_good == 0U) || (hr_age_ms > PPG_HR_HOLD_TIMEOUT_MS))
    {
      PPG_ClearHrState();
      last_oled_vitals_update_ms = 0U;
    }
  }
}

static void Vitals_LogStatus(void)
{
#if DIAG_STREAM_USB_ENABLE != 0U
  DiagnosticStats_t stats = diag_stats;
  uint8_t autocorr_active = (ppg_autocorr_job.phase != PPG_AUTOCORR_JOB_IDLE) ? 1U : 0U;
  char line[320];
  int len;

  Diagnostics_ResetInterval();
  Diagnostics_WriteHeaderIfNeeded();
  if (CDC_IsPortOpen_FS() == 0U)
  {
    return;
  }

  len = snprintf(line,
                 sizeof(line),
                 "%lu,%lu,%lu,%lu,%lu,%lu,%lu,%lu,%lu,%lu,%u,%lu,%lu,%lu,%lu,%lu,%lu,%lu,%lu,%lu,%lu,%lu,%u,%u,%d,%d,%d\r\n",
                 (unsigned long)HAL_GetTick(),
                 (unsigned long)stats.loop_gap_max_ms,
                 (unsigned long)stats.loop_count,
                 (unsigned long)stats.max30102_service_calls,
                 (unsigned long)stats.max30102_samples_read,
                 (unsigned long)stats.max30102_fifo_max,
                 (unsigned long)stats.max30102_service_max_ms,
                 (unsigned long)stats.autocorr_service_calls,
                 (unsigned long)stats.autocorr_jobs_done,
                 (unsigned long)stats.autocorr_service_max_ms,
                 (unsigned int)autocorr_active,
                 (unsigned long)stats.oled091_flush_count,
                 (unsigned long)stats.oled091_flush_max_ms,
                 (unsigned long)stats.oled64_flush_count,
                 (unsigned long)stats.oled64_flush_max_ms,
                 (unsigned long)stats.cdc_write_calls,
                 (unsigned long)stats.cdc_busy_count,
                 (unsigned long)stats.cdc_timeout_count,
                 (unsigned long)stats.cdc_write_max_wait_ms,
                 (unsigned long)stats.cdc_write_total_wait_ms,
                 (unsigned long)max30102_irq_count,
                 (unsigned long)max30102_samples_total,
                 (unsigned int)finger_present,
                 (unsigned int)ppg_signal_valid,
                 (int)heart_rate_bpm,
                 (int)hr_autocorr_bpm,
                 (int)spo2_percent);
  if (len <= 0)
  {
    return;
  }
  if ((size_t)len >= sizeof(line))
  {
    len = (int)sizeof(line) - 1;
  }

  (void)CDC_Write((uint8_t *)line, (uint16_t)len);
#elif PPG_PROC_STREAM_USB_ENABLE != 0U
  static const char header[] =
      "PPG_PROC_HEADER,tick_ms,finger,raw_finger,valid,hr_confirmed,hr_confirm_count,hr_hist_count,"
      "samples,since_finger,red,ir,red_dc,ir_dc,red_ac,ir_ac,red_filt,ir_filt,red_rms,ir_rms,"
      "amp,score_x100,hr,auto,spo2,ratio_x1000,last_hr_age_ms,autocorr_phase,robust_peaks,"
      "af_risk,stress_index,af_ppi_count,stress_ppi_count,af_last_ppi_ms,af_last_ppi_ok,af_ppi_rejects,"
      "stress_last_ppi_ms,stress_last_ppi_ok,stress_ppi_rejects\r\n";
  uint32_t now_ms = HAL_GetTick();
  uint32_t since_finger = 0U;
  uint32_t last_hr_age_ms = 0U;
  char line[512];
  int len;

  if (CDC_IsPortOpen_FS() == 0U)
  {
    ppg_proc_header_sent = 0U;
    return;
  }

  if ((finger_present != 0U) && (max30102_samples_total >= ppg_state.finger_start_sample))
  {
    since_finger = max30102_samples_total - ppg_state.finger_start_sample;
  }
  if (ppg_state.last_hr_update_tick_ms != 0U)
  {
    last_hr_age_ms = now_ms - ppg_state.last_hr_update_tick_ms;
  }

  if (ppg_proc_header_sent == 0U)
  {
    if (CDC_Write((const uint8_t *)header, (uint16_t)(sizeof(header) - 1U)) != USBD_OK)
    {
      return;
    }
    ppg_proc_header_sent = 1U;
  }

  len = snprintf(line,
                 sizeof(line),
                 "PPG_PROC,%lu,%u,%u,%u,%u,%u,%u,%lu,%lu,%lu,%lu,%ld,%ld,%ld,%ld,%ld,%ld,%ld,%ld,%u,%u,%d,%d,%d,%u,%lu,%u,%u,%d,%d,%u,%u,%u,%u,%lu,%u,%u,%lu\r\n",
                 (unsigned long)now_ms,
                 (unsigned int)finger_present,
                 (unsigned int)ppg_state.raw_finger_present,
                 (unsigned int)ppg_signal_valid,
                 (unsigned int)ppg_state.hr_confirmed,
                 (unsigned int)ppg_state.hr_confirm_count,
                 (unsigned int)ppg_state.hr_history_count,
                 (unsigned long)max30102_samples_total,
                 (unsigned long)since_finger,
                 (unsigned long)max30102_last_red_raw,
                 (unsigned long)max30102_last_ir_raw,
                 (long)PPG_RoundToInt32(ppg_state.red_dc),
                 (long)PPG_RoundToInt32(ppg_state.ir_dc),
                 (long)PPG_RoundToInt32(ppg_state.red_ac),
                 (long)PPG_RoundToInt32(ppg_state.ir_ac),
                 (long)PPG_RoundToInt32(ppg_state.red_filt),
                 (long)PPG_RoundToInt32(ppg_state.ir_filt),
                 (long)PPG_RoundToInt32(ppg_state.red_rms),
                 (long)PPG_RoundToInt32(ppg_state.ir_rms),
                 (unsigned int)ppg_signal_amp,
                 (unsigned int)ppg_finger_score_x100,
                 (int)heart_rate_bpm,
                 (int)hr_autocorr_bpm,
                 (int)spo2_percent,
                 (unsigned int)ppg_ratio_x1000,
                 (unsigned long)last_hr_age_ms,
                 (unsigned int)ppg_autocorr_job.phase,
                 (unsigned int)ppg_robust_peak.peak_count,
                 (int)af_risk_percent,
                 (int)stress_hrv_index,
                 (unsigned int)af_live_ppi_count,
                 (unsigned int)stress_live_ppi_count,
                 (unsigned int)af_live_last_interval_ms,
                 (unsigned int)af_live_last_interval_accepted,
                 (unsigned long)af_live_rejected_ppi_count,
                 (unsigned int)stress_live_last_interval_ms,
                 (unsigned int)stress_live_last_interval_accepted,
                 (unsigned long)stress_live_rejected_ppi_count);
  if (len <= 0)
  {
    return;
  }
  if ((size_t)len >= sizeof(line))
  {
    len = (int)sizeof(line) - 1;
  }

  (void)CDC_Write((uint8_t *)line, (uint16_t)len);
#else
  CDC_Printf("PPG finger=%u valid=%u red=%lu ir=%lu ir_rms=%u score=%u.%02u hr=%d auto=%d spo2=%d ratio=%u.%03u irq=%lu samples=%lu\r\n",
             (unsigned int)finger_present,
             (unsigned int)ppg_signal_valid,
             (unsigned long)max30102_last_red_raw,
             (unsigned long)max30102_last_ir_raw,
             (unsigned int)ppg_signal_amp,
             (unsigned int)(ppg_finger_score_x100 / 100U),
             (unsigned int)(ppg_finger_score_x100 % 100U),
             (int)heart_rate_bpm,
             (int)hr_autocorr_bpm,
             (int)spo2_percent,
             (unsigned int)(ppg_ratio_x1000 / 1000U),
             (unsigned int)(ppg_ratio_x1000 % 1000U),
             (unsigned long)max30102_irq_count,
             (unsigned long)max30102_samples_total);
#endif
}

static float ClampFloat(float value, float low, float high)
{
  if (value < low)
  {
    return low;
  }
  if (value > high)
  {
    return high;
  }
  return value;
}

static uint8_t ClampU8(int16_t value, uint8_t low, uint8_t high)
{
  if (value < (int16_t)low)
  {
    return low;
  }
  if (value > (int16_t)high)
  {
    return high;
  }
  return (uint8_t)value;
}

void HAL_GPIO_EXTI_Callback(uint16_t GPIO_Pin)
{
  if (GPIO_Pin == GPIO_PIN_5)
  {
    max30102_irq_count++;
    last_max30102_irq_ms = HAL_GetTick();
    max30102_int_pending = 1U;
  }
}

/* USER CODE END 4 */

/**
  * @brief  This function is executed in case of error occurrence.
  * @retval None
  */
void Error_Handler(void)
{
  /* USER CODE BEGIN Error_Handler_Debug */
  /* User can add his own implementation to report the HAL error return state */
  __disable_irq();
  while (1)
  {
  }
  /* USER CODE END Error_Handler_Debug */
}

#ifdef  USE_FULL_ASSERT
/**
  * @brief  Reports the name of the source file and the source line number
  *         where the assert_param error has occurred.
  * @param  file: pointer to the source file name
  * @param  line: assert_param error line source number
  * @retval None
  */
void assert_failed(uint8_t *file, uint32_t line)
{
  /* USER CODE BEGIN 6 */
  /* User can add his own implementation to report the file name and line number,
     ex: printf("Wrong parameters value: file %s on line %d\r\n", file, line) */
  /* USER CODE END 6 */
}
#endif /* USE_FULL_ASSERT */
