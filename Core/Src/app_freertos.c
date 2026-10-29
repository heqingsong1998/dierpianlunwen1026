/* USER CODE BEGIN Header */
/**
  ******************************************************************************
  * File Name          : app_freertos.c
  * Description        : Code for freertos applications
  ******************************************************************************
  * @attention
  *
  * Copyright (c) 2025 STMicroelectronics.
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
#include "FreeRTOS.h"
#include "task.h"
#include "main.h"
#include "cmsis_os.h"

/* Private includes ----------------------------------------------------------*/
/* USER CODE BEGIN Includes */
#include "AD7142.h"
#include "queue.h"
#include "can.h"
#include "host_control_protocol.h"
#include <string.h>
/* USER CODE END Includes */

/* Private typedef -----------------------------------------------------------*/
/* USER CODE BEGIN PTD */
typedef struct {
    int16_t legacy_values[16];
    int16_t stage_pair_sum[8];
    uint32_t sequence;
    uint32_t ready_us;
    uint16_t baseline_generation;
} CalclptData;

typedef struct {
    uint32_t sequence;
    uint32_t ready_us;
    uint16_t baseline_generation;
} SampleTiming;

typedef enum {
	EMAG_MODE_HOLD = 0,
	EMAG_MODE_FORCE_RESET = 1,
	EMAG_MODE_FORCE_SET = 2
} EmagControlMode;

extern uint16_t offset1[10],offset2[10];
static uint8_t rxdata[64];
static volatile uint8_t emag_mode = EMAG_MODE_HOLD;
static volatile uint8_t baseline_reset_request = 0;
static volatile uint8_t emag_pin_state = 1;
static volatile uint16_t baseline_generation = 0;
static volatile uint32_t dropped_commands = 0;
static uint32_t dropped_tx_frames = 0;
static QueueHandle_t command_queue = NULL;
static SampleTiming sample_history[128];
/* USER CODE END PTD */

/* Private define ------------------------------------------------------------*/
/* USER CODE BEGIN PD */

/* 两片 AD7142 均使用 AD7142_init() 中的 10 Stage 配置：
 * 第一路接近觉：CIN9 -> Stage7 -> JJJ1_1 / JJJ2_1（0x123 int16 索引 5 / 12）。
 * 第二路接近觉：CIN4 -> Stage4 -> JJJ1_2 / JJJ2_2（0x123 int16 索引 6 / 13）。
 * CIN 编号是输入通道号，不是芯片封装的物理脚号，也不等于 Stage 编号。
 * Stage9 对应 CIN11，参与受力信号计算，不能当作 CIN9 的接近觉上传。
 * 接近觉测试版的 5 Stage 配置把 CIN9 放在 Stage3；勿将该索引搬到本版本。
 * 修改通道配置时，应同时核对 Drivers/AD7142/AD7142.c 的 AD7142_init()。
 */
#define AD7142_PROXIMITY_CIN9_STAGE 7U
#define AD7142_PROXIMITY_CIN4_STAGE 4U

/* USER CODE END PD */

/* Private macro -------------------------------------------------------------*/
/* USER CODE BEGIN PM */

/* USER CODE END PM */

/* Private variables ---------------------------------------------------------*/
/* USER CODE BEGIN Variables */

/* USER CODE END Variables */
/* Definitions for ControlTask */
osThreadId_t ControlTaskHandle;
const osThreadAttr_t ControlTask_attributes = {
  .name = "ControlTask",
  .priority = (osPriority_t) osPriorityAboveNormal,
  .stack_size = 512 * 4
};
/* Definitions for CANCommunicatio */
osThreadId_t CANCommunicatioHandle;
const osThreadAttr_t CANCommunicatio_attributes = {
  .name = "CANCommunicatio",
  .priority = (osPriority_t) osPriorityNormal,
  .stack_size = 256 * 4
};
/* Definitions for Data */
osMessageQueueId_t DataHandle;
const osMessageQueueAttr_t Data_attributes = {
  .name = "Data"
};

/* Private function prototypes -----------------------------------------------*/
/* USER CODE BEGIN FunctionPrototypes */
static void Emag_SetOutput(uint8_t set_pin);
static void UpdateBaselineFromCurrentSample(const uint16_t *data1, const uint16_t *data2);
static void Timing_Init(void);
static uint32_t Timing_NowUs(void);
static void ProcessHostCommand(const HostControlCommand *command);
static void SendSample(const CalclptData *sample);
static void SendFrame(uint16_t can_id, const uint8_t *data, uint32_t dlc);

/* USER CODE END FunctionPrototypes */

void Task_Control(void *argument);
void Task_CAN(void *argument);

void MX_FREERTOS_Init(void); /* (MISRA C 2004 rule 8.1) */

/**
  * @brief  FreeRTOS initialization
  * @param  None
  * @retval None
  */
void MX_FREERTOS_Init(void) {
  /* USER CODE BEGIN Init */
  Timing_Init();
  /* USER CODE END Init */
  /* USER CODE BEGIN RTOS_MUTEX */
  /* add mutexes, ... */
  /* USER CODE END RTOS_MUTEX */

  /* USER CODE BEGIN RTOS_SEMAPHORES */
  /* add semaphores, ... */
  /* USER CODE END RTOS_SEMAPHORES */

  /* USER CODE BEGIN RTOS_TIMERS */
  /* start timers, add new ones, ... */
  /* USER CODE END RTOS_TIMERS */

  /* Create the queue(s) */
  /* creation of Data */
  DataHandle = osMessageQueueNew (1, sizeof(CalclptData), &Data_attributes);

  /* USER CODE BEGIN RTOS_QUEUES */
  command_queue = xQueueCreate(8, sizeof(HostControlCommand));
  configASSERT(DataHandle != NULL);
  configASSERT(command_queue != NULL);
  /* USER CODE END RTOS_QUEUES */

  /* Create the thread(s) */
  /* creation of ControlTask */
  ControlTaskHandle = osThreadNew(Task_Control, NULL, &ControlTask_attributes);

  /* creation of CANCommunicatio */
  CANCommunicatioHandle = osThreadNew(Task_CAN, NULL, &CANCommunicatio_attributes);

  /* USER CODE BEGIN RTOS_THREADS */
  /* add threads, ... */
  /* USER CODE END RTOS_THREADS */

  /* USER CODE BEGIN RTOS_EVENTS */
  /* add events, ... */
  /* USER CODE END RTOS_EVENTS */

}

/* USER CODE BEGIN Header_Task_Control */
/**
  * @brief  Acquire and zero sensor signals; all decisions belong to the host.
  * @param  argument: Not used
  * @retval None
  */
/* USER CODE END Header_Task_Control */
void Task_Control(void *argument)
{
  /* USER CODE BEGIN Task_Control */
    uint16_t data1[10];
    uint16_t data2[10];
    int16_t signals[2][10];
    CalclptData sample;
    uint32_t sequence = 0;
    TickType_t last_wake_time = xTaskGetTickCount();
  /* Infinite loop */
  for(;;)
  {
        AD7142_Read1(CDC_RESULT_S0, data1, 10);
        AD7142_Read2(CDC_RESULT_S0, data2, 10);

        if (baseline_reset_request != 0U) {
            baseline_reset_request = 0U;
            UpdateBaselineFromCurrentSample(data1, data2);
            baseline_generation++;
        }

        for (int i = 0; i < 10; i++) {
            signals[0][i] = (int16_t)((int32_t)data1[i] - offset1[i]);
            signals[1][i] = (int16_t)((int32_t)data2[i] - offset2[i]);
        }

        /* Preserve the original 0x123 channel order, including its Fx/Fy swap.
           These are zeroed integer combinations, not calibrated forces. */
        for (int foot = 0; foot < 2; foot++) {
            const int16_t *s = signals[foot];
            int16_t *out = &sample.legacy_values[foot * 7];
            int16_t *pairs = &sample.stage_pair_sum[foot * 4];
            out[0] = s[5] - s[6] + s[2] - s[3];
            out[1] = s[1] - s[0] + s[9] - s[8];
            out[2] = s[0] + s[1] + s[2] + s[3] + s[8] + s[9] + s[5] + s[6];
            out[3] = s[0] + s[1] + s[2] + s[3] - s[8] - s[9] - s[6] - s[5];
            out[4] = s[0] + s[1] + s[5] + s[6] - s[2] - s[3] - s[8] - s[9];
            /* 接近觉分别上传各自清零后的单 Stage 读数，两片芯片共用此映射。 */
            out[5] = s[AD7142_PROXIMITY_CIN9_STAGE]; /* CIN9，第一路接近觉。 */
            out[6] = s[AD7142_PROXIMITY_CIN4_STAGE]; /* CIN4，第二路接近觉。 */
            pairs[0] = s[0] + s[1];
            pairs[1] = s[2] + s[3];
            pairs[2] = s[8] + s[9];
            pairs[3] = s[5] + s[6];
        }
        sample.legacy_values[14] = sample.legacy_values[2];
        sample.legacy_values[15] = sample.legacy_values[9];

        /* A: data ready, before the queue, CAN packing and CAN transmission.
           Publish timing and generation together before making the sample visible. */
        taskENTER_CRITICAL();
        sequence++;
        if (sequence == 0U) {
            sequence = 1U;
        }
        sample.sequence = sequence;
        sample.ready_us = Timing_NowUs();
        sample.baseline_generation = baseline_generation;
        sample_history[sequence & 127U].sequence = sequence;
        sample_history[sequence & 127U].ready_us = sample.ready_us;
        sample_history[sequence & 127U].baseline_generation = baseline_generation;
        taskEXIT_CRITICAL();

        xQueueOverwrite((QueueHandle_t)DataHandle, &sample);
        xTaskNotifyGive((TaskHandle_t)CANCommunicatioHandle);
        vTaskDelayUntil(&last_wake_time, pdMS_TO_TICKS(16));
  }
  /* USER CODE END Task_Control */
}

/* USER CODE BEGIN Header_Task_CAN */
/**
* @brief Function implementing the CANCommunicatio thread.
* @param argument: Not used
* @retval None
*/
/* USER CODE END Header_Task_CAN */
void Task_CAN(void *argument)
{
  /* USER CODE BEGIN Task_CAN */
    CalclptData sample;
    HostControlCommand command;
  /* Infinite loop */
  for(;;)
  {
        ulTaskNotifyTake(pdTRUE, portMAX_DELAY);
        /* This task owns all application TX and GPIO writes. RX interrupts wake
           it for commands, independently of the 16 ms acquisition schedule. */
        for (unsigned int i = 0; i < 8U; i++) {
            if (xQueueReceive(command_queue, &command, 0) != pdTRUE) {
                break;
            }
            ProcessHostCommand(&command);
        }
        if (xQueueReceive((QueueHandle_t)DataHandle, &sample, 0) == pdTRUE) {
            SendSample(&sample);
        }
  }
  /* USER CODE END Task_CAN */
}

/* Private application code --------------------------------------------------*/
/* USER CODE BEGIN Application */

void HAL_FDCAN_RxFifo0Callback(FDCAN_HandleTypeDef *hfdcan, uint32_t RxFifo0ITs)
{
    FDCAN_RxHeaderTypeDef header;
    HostControlCommand command;
    BaseType_t higher_priority_task_woken = pdFALSE;

    if ((RxFifo0ITs & FDCAN_IT_RX_FIFO0_NEW_MESSAGE) == 0U ||
        hfdcan->Instance != FDCAN1) {
        return;
    }

    while (HAL_FDCAN_GetRxFifoFillLevel(hfdcan, FDCAN_RX_FIFO0) != 0U) {
        if (HAL_FDCAN_GetRxMessage(hfdcan, FDCAN_RX_FIFO0, &header, rxdata) != HAL_OK) {
            break;
        }
        /* Drain unrelated frames too, but never interpret them as control data.
           The 64-byte buffer also accommodates unrelated CAN FD frames. */
        if (header.IdType != FDCAN_EXTENDED_ID ||
            header.Identifier != HOST_COMMAND_CAN_ID ||
            header.RxFrameType != FDCAN_DATA_FRAME ||
            header.DataLength != FDCAN_DLC_BYTES_8 ||
            command_queue == NULL || CANCommunicatioHandle == NULL) {
            continue;
        }
        if (HostProtocol_DecodeCommand(rxdata, &command) == 0U) {
            continue;
        }
        /* D: valid command decoded in RX ISR, before task scheduling or GPIO. */
        command.received_us = Timing_NowUs();
        if (xQueueSendFromISR(command_queue, &command, &higher_priority_task_woken) == pdTRUE) {
            vTaskNotifyGiveFromISR((TaskHandle_t)CANCommunicatioHandle,
                                  &higher_priority_task_woken);
        } else {
            dropped_commands++;
        }
    }
    portYIELD_FROM_ISR(higher_priority_task_woken);
}

static void Timing_Init(void)
{
    uint32_t timer_clock = HAL_RCC_GetPCLK1Freq();

    /* TIM2 is an otherwise unused 32-bit timer; do not modify HAL's TIM6 tick. */
    if ((RCC->CFGR & RCC_CFGR_PPRE1) != 0U) {
        timer_clock *= 2U;
    }
    configASSERT(timer_clock >= 1000000U && timer_clock % 1000000U == 0U);
    __HAL_RCC_TIM2_CLK_ENABLE();
    TIM2->CR1 = 0U;
    TIM2->DIER = 0U;
    TIM2->PSC = timer_clock / 1000000U - 1U;
    TIM2->ARR = 0xFFFFFFFFUL;
    TIM2->EGR = TIM_EGR_UG;
    TIM2->SR = 0U;
    TIM2->CNT = 0U;
    TIM2->CR1 = TIM_CR1_CEN;
}

static uint32_t Timing_NowUs(void)
{
    return TIM2->CNT;
}

static void SendFrame(uint16_t can_id, const uint8_t *data, uint32_t dlc)
{
    TickType_t started = xTaskGetTickCount();
    HAL_StatusTypeDef result;

    do {
        result = canfd_send_bytes(can_id, data, dlc);
        if (result != HAL_BUSY) {
            break;
        }
        if ((TickType_t)(xTaskGetTickCount() - started) >= pdMS_TO_TICKS(5)) {
            break;
        }
        vTaskDelay(pdMS_TO_TICKS(1));
    } while (1);
    if (result != HAL_OK) {
        /* Exposed in 0x124; a full TX FIFO must not halt acquisition/control. */
        dropped_tx_frames++;
    }
}

static void SendSample(const CalclptData *sample)
{
    uint8_t data[48] = {0};
    uint8_t pairs[32] = {0};

    for (unsigned int i = 0; i < 16U; i++) {
        HostProtocol_PutU16(&data[i * 2U], (uint16_t)sample->legacy_values[i]);
    }
    /* Removed local force-threshold flags remain zero at indices 16 and 17. */
    HostProtocol_PutU16(&data[36], emag_mode);
    HostProtocol_PutU16(&data[38], emag_pin_state);
    HostProtocol_PutU32(&data[40], sample->sequence);
    HostProtocol_PutU32(&data[44], sample->ready_us);
    SendFrame(HOST_DATA_CAN_ID, data, FDCAN_DLC_BYTES_48);

    for (unsigned int i = 0; i < 8U; i++) {
        HostProtocol_PutU16(&pairs[i * 2U], (uint16_t)sample->stage_pair_sum[i]);
    }
    HostProtocol_PutU32(&pairs[16], sample->sequence);
    HostProtocol_PutU32(&pairs[20], sample->ready_us);
    HostProtocol_PutU16(&pairs[24], sample->baseline_generation);
    HostProtocol_PutU16(&pairs[26], HOST_PROTOCOL_VERSION);
    HostProtocol_PutU16(&pairs[28], (uint16_t)dropped_commands);
    HostProtocol_PutU16(&pairs[30], (uint16_t)dropped_tx_frames);
    SendFrame(HOST_STAGE_PAIR_CAN_ID, pairs, FDCAN_DLC_BYTES_32);
}

static void ProcessHostCommand(const HostControlCommand *command)
{
    HostControlAck ack = {0};
    uint8_t data[HOST_ACK_SIZE];
    const SampleTiming *timing;

    ack.command_id = command->command_id;
    ack.sample_sequence = command->sample_sequence;
    ack.received_us = command->received_us;
    ack.action = command->action;
    ack.ready_us = HOST_TIME_INVALID;
    ack.gpio_written_us = HOST_TIME_INVALID;
    ack.round_trip_us = HOST_TIME_INVALID;
    if (command->legacy != 0U) {
        ack.flags |= HOST_ACK_FLAG_LEGACY;
    }

    /* Acquisition may preempt this task. Validate the history/generation and
       apply the command atomically with respect to publishing/zeroing samples. */
    taskENTER_CRITICAL();
    if (command->sample_sequence != 0U) {
        timing = &sample_history[command->sample_sequence & 127U];
        if (timing->sequence != command->sample_sequence) {
            ack.status = HOST_ACK_UNKNOWN_SAMPLE;
        } else if (timing->baseline_generation != baseline_generation) {
            ack.status = HOST_ACK_BASELINE_CHANGED;
        } else {
            ack.ready_us = timing->ready_us;
            /* Unsigned subtraction handles the 32-bit microsecond wrap. */
            ack.round_trip_us = command->received_us - timing->ready_us;
            ack.flags |= HOST_ACK_FLAG_TIMING_VALID;
        }
    }

    if (ack.status == HOST_ACK_OK) {
        if (command->zero_requested != 0U) {
            baseline_reset_request = 1U;
            ack.flags |= HOST_ACK_FLAG_ZERO_PENDING;
        }
        if (command->action == HOST_ACTION_LOW || command->action == HOST_ACTION_HIGH) {
            if (emag_pin_state != command->action) {
                ack.flags |= HOST_ACK_FLAG_GPIO_CHANGED;
            }
            Emag_SetOutput(command->action);
            ack.gpio_written_us = Timing_NowUs();
            ack.flags |= HOST_ACK_FLAG_GPIO_WRITTEN;
            emag_mode = (command->action == HOST_ACTION_LOW) ?
                        EMAG_MODE_FORCE_RESET : EMAG_MODE_FORCE_SET;
        }
        /* HOLD and ZERO never change the GPIO or enable autonomous control. */
    }
    ack.pin_state = emag_pin_state;
    ack.baseline_generation = baseline_generation;
    ack.dropped_commands = (uint16_t)dropped_commands;
    taskEXIT_CRITICAL();

    HostProtocol_EncodeAck(data, &ack);
    SendFrame(HOST_ACK_CAN_ID, data, FDCAN_DLC_BYTES_32);
}

static void Emag_SetOutput(uint8_t set_pin)
{
	if(set_pin != 0U)
	{
		LL_GPIO_SetOutputPin(Emagctrl_GPIO_Port, Emagctrl_Pin);
		emag_pin_state = 1U;
	}
	else
	{
		LL_GPIO_ResetOutputPin(Emagctrl_GPIO_Port, Emagctrl_Pin);
		emag_pin_state = 0U;
	}
}

static void UpdateBaselineFromCurrentSample(const uint16_t *data1, const uint16_t *data2)
{
	for(uint8_t i = 0; i < 10U; i++)
	{
		offset1[i] = data1[i];
		offset2[i] = data2[i];
	}
}
/* USER CODE END Application */

