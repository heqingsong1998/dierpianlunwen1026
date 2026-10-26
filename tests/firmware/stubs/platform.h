#ifndef TEST_PLATFORM_H
#define TEST_PLATFORM_H
#include <assert.h>
#include <stddef.h>
#include <stdint.h>

typedef uint32_t TickType_t;
typedef int BaseType_t;
typedef void *TaskHandle_t;
typedef void *QueueHandle_t;
typedef void *osThreadId_t;
typedef void *osMessageQueueId_t;
typedef int osPriority_t;
typedef struct { const char *name; osPriority_t priority; uint32_t stack_size; } osThreadAttr_t;
typedef struct { const char *name; } osMessageQueueAttr_t;
#define osPriorityAboveNormal 1
#define osPriorityNormal 0
#define pdTRUE 1
#define pdFALSE 0
#define portMAX_DELAY UINT32_MAX
#define pdMS_TO_TICKS(x) ((TickType_t)(x))
#define configASSERT(x) assert(x)
extern unsigned int test_critical_depth;
#define taskENTER_CRITICAL() (++test_critical_depth)
#define taskEXIT_CRITICAL() do { assert(test_critical_depth > 0); --test_critical_depth; } while (0)
#define portYIELD_FROM_ISR(x) ((void)(x))

QueueHandle_t xQueueCreate(unsigned int length, unsigned int item_size);
BaseType_t xQueueOverwrite(QueueHandle_t queue, const void *item);
BaseType_t xQueueReceive(QueueHandle_t queue, void *item, TickType_t wait);
BaseType_t xQueueSendFromISR(QueueHandle_t queue, const void *item, BaseType_t *woken);
BaseType_t xTaskNotifyGive(TaskHandle_t task);
void vTaskNotifyGiveFromISR(TaskHandle_t task, BaseType_t *woken);
uint32_t ulTaskNotifyTake(BaseType_t clear, TickType_t wait);
TickType_t xTaskGetTickCount(void);
void vTaskDelay(TickType_t ticks);
void vTaskDelayUntil(TickType_t *previous, TickType_t ticks);
osMessageQueueId_t osMessageQueueNew(uint32_t count, uint32_t size, const osMessageQueueAttr_t *attr);
osThreadId_t osThreadNew(void (*entry)(void *), void *argument, const osThreadAttr_t *attr);

typedef struct { volatile uint32_t CR1, DIER, PSC, ARR, EGR, SR, CNT; } TestTimer;
typedef struct { volatile uint32_t CFGR; } TestRcc;
extern TestTimer test_timer;
extern TestRcc test_rcc;
#define TIM2 (&test_timer)
#define RCC (&test_rcc)
#define RCC_CFGR_PPRE1 0x700U
#define TIM_EGR_UG 1U
#define TIM_CR1_CEN 1U
#define __HAL_RCC_TIM2_CLK_ENABLE() ((void)0)
uint32_t HAL_RCC_GetPCLK1Freq(void);

typedef enum { HAL_OK, HAL_ERROR, HAL_BUSY, HAL_TIMEOUT } HAL_StatusTypeDef;
typedef struct { void *Instance; } FDCAN_HandleTypeDef;
typedef struct { uint32_t IdType, Identifier, RxFrameType, DataLength; } FDCAN_RxHeaderTypeDef;
#define FDCAN1 ((void *)(uintptr_t)1U)
#define FDCAN_STANDARD_ID 0U
#define FDCAN_EXTENDED_ID 1U
#define FDCAN_DATA_FRAME 0U
#define FDCAN_REMOTE_FRAME 1U
#define FDCAN_IT_RX_FIFO0_NEW_MESSAGE 1U
#define FDCAN_RX_FIFO0 0x40U
#define FDCAN_DLC_BYTES_8 8U
#define FDCAN_DLC_BYTES_32 13U
#define FDCAN_DLC_BYTES_48 14U
#define FDCAN_DLC_BYTES_64 15U
uint32_t HAL_FDCAN_GetRxFifoFillLevel(const FDCAN_HandleTypeDef *handle, uint32_t fifo);
HAL_StatusTypeDef HAL_FDCAN_GetRxMessage(FDCAN_HandleTypeDef *handle, uint32_t fifo,
                                       FDCAN_RxHeaderTypeDef *header, uint8_t *data);
HAL_StatusTypeDef canfd_send_bytes(uint16_t id, const uint8_t *data, uint32_t dlc);
#define Emagctrl_GPIO_Port ((void *)(uintptr_t)2U)
#define Emagctrl_Pin 16U
void LL_GPIO_SetOutputPin(void *port, uint32_t pin);
void LL_GPIO_ResetOutputPin(void *port, uint32_t pin);
#define CDC_RESULT_S0 0xBU
uint16_t AD7142_Read1(uint16_t address, uint16_t *data, uint16_t size);
uint16_t AD7142_Read2(uint16_t address, uint16_t *data, uint16_t size);
#endif
