/* Execute the actual firmware acquisition, decoder, RX and command handlers on
   the host. Only hardware and RTOS services are replaced by the stubs. */
#include <setjmp.h>
#include <stdio.h>
#include <string.h>
#include "platform.h"
#include "../../Core/Src/app_freertos.c"

unsigned int test_critical_depth;
TestTimer test_timer;
TestRcc test_rcc;
uint16_t offset1[10], offset2[10];
static uint16_t adc_values[2][10];
static unsigned int gpio_writes, gpio_level, notifications, thread_count;
static TickType_t ticks;
static unsigned int acquisitions_left, busy_tx_attempts;
static jmp_buf acquisition_done;
typedef struct { unsigned int count, head, capacity, size; uint8_t items[2048]; } TestQueue;
static TestQueue queues[4];
static unsigned int queues_used;
typedef struct { uint16_t id; uint32_t dlc; uint8_t data[64]; } TestTx;
static TestTx tx_frames[16];
static unsigned int tx_count;
typedef struct { FDCAN_RxHeaderTypeDef header; uint8_t data[64]; } TestRx;
static TestRx rx_frames[16];
static unsigned int rx_count, rx_head;

QueueHandle_t xQueueCreate(unsigned int length, unsigned int item_size)
{
    TestQueue *q = &queues[queues_used++];
    assert(queues_used <= 4 && length * item_size <= sizeof(q->items));
    memset(q, 0, sizeof(*q)); q->capacity = length; q->size = item_size;
    return q;
}
BaseType_t xQueueOverwrite(QueueHandle_t handle, const void *item)
{
    TestQueue *q = handle; assert(q->capacity == 1);
    memcpy(q->items, item, q->size); q->count = 1; q->head = 0; return pdTRUE;
}
BaseType_t xQueueReceive(QueueHandle_t handle, void *item, TickType_t wait)
{
    TestQueue *q = handle; (void)wait;
    if (!q->count) return pdFALSE;
    memcpy(item, &q->items[q->head * q->size], q->size);
    q->head = (q->head + 1) % q->capacity; --q->count; return pdTRUE;
}
BaseType_t xQueueSendFromISR(QueueHandle_t handle, const void *item, BaseType_t *woken)
{
    TestQueue *q = handle; (void)woken;
    if (q->count == q->capacity) return pdFALSE;
    memcpy(&q->items[((q->head + q->count) % q->capacity) * q->size], item, q->size);
    ++q->count; return pdTRUE;
}
osMessageQueueId_t osMessageQueueNew(uint32_t count, uint32_t size, const osMessageQueueAttr_t *attr)
{ (void)attr; return xQueueCreate(count, size); }
osThreadId_t osThreadNew(void (*entry)(void *), void *argument, const osThreadAttr_t *attr)
{ (void)entry; (void)argument; (void)attr; return (void *)(uintptr_t)++thread_count; }
BaseType_t xTaskNotifyGive(TaskHandle_t task) { assert(task); ++notifications; return pdTRUE; }
void vTaskNotifyGiveFromISR(TaskHandle_t task, BaseType_t *woken)
{ (void)woken; assert(task); ++notifications; }
uint32_t ulTaskNotifyTake(BaseType_t clear, TickType_t wait)
{ (void)clear; (void)wait; return 1; }
TickType_t xTaskGetTickCount(void) { return ticks; }
void vTaskDelay(TickType_t delay) { ticks += delay; test_timer.CNT += 1000U * delay; }
void vTaskDelayUntil(TickType_t *previous, TickType_t delay)
{
    assert(delay == 16U); *previous += delay;
    if (--acquisitions_left == 0) longjmp(acquisition_done, 1);
    ticks += delay; test_timer.CNT += 1000U * delay;
}
uint32_t HAL_RCC_GetPCLK1Freq(void) { return 24000000U; }
void LL_GPIO_SetOutputPin(void *port, uint32_t pin)
{ (void)port; (void)pin; assert(test_critical_depth); ++gpio_writes; gpio_level = 1; }
void LL_GPIO_ResetOutputPin(void *port, uint32_t pin)
{ (void)port; (void)pin; assert(test_critical_depth); ++gpio_writes; gpio_level = 0; }
uint16_t AD7142_Read1(uint16_t address, uint16_t *data, uint16_t size)
{ assert(address == CDC_RESULT_S0 && size == 10); memcpy(data, adc_values[0], 20); return 1; }
uint16_t AD7142_Read2(uint16_t address, uint16_t *data, uint16_t size)
{ assert(address == CDC_RESULT_S0 && size == 10); memcpy(data, adc_values[1], 20); return 1; }
HAL_StatusTypeDef canfd_send_bytes(uint16_t id, const uint8_t *data, uint32_t dlc)
{
    unsigned int size = dlc == FDCAN_DLC_BYTES_48 ? 48 : dlc == FDCAN_DLC_BYTES_32 ? 32 : 8;
    if (busy_tx_attempts) { --busy_tx_attempts; return HAL_BUSY; }
    assert(tx_count < 16); tx_frames[tx_count].id = id; tx_frames[tx_count].dlc = dlc;
    memcpy(tx_frames[tx_count++].data, data, size); return HAL_OK;
}
uint32_t HAL_FDCAN_GetRxFifoFillLevel(const FDCAN_HandleTypeDef *handle, uint32_t fifo)
{ (void)handle; (void)fifo; return rx_count - rx_head; }
HAL_StatusTypeDef HAL_FDCAN_GetRxMessage(FDCAN_HandleTypeDef *handle, uint32_t fifo,
                                       FDCAN_RxHeaderTypeDef *header, uint8_t *data)
{
    (void)handle; (void)fifo; assert(rx_head < rx_count);
    *header = rx_frames[rx_head].header; memcpy(data, rx_frames[rx_head++].data, 64); return HAL_OK;
}

static void ResetFixture(void)
{
    memset(queues, 0, sizeof(queues)); queues_used = 0; thread_count = 0;
    memset(sample_history, 0, sizeof(sample_history));
    memset(&test_timer, 0, sizeof(test_timer)); memset(&test_rcc, 0, sizeof(test_rcc));
    memset(offset1, 0, sizeof(offset1)); memset(offset2, 0, sizeof(offset2));
    for (unsigned int f = 0; f < 2; ++f)
        for (unsigned int i = 0; i < 10; ++i) adc_values[f][i] = (uint16_t)(1000 + 10 * i + f);
    baseline_generation = 0; baseline_reset_request = 0; emag_pin_state = 0;
    emag_mode = EMAG_MODE_HOLD; dropped_commands = 0; dropped_tx_frames = 0;
    gpio_level = 0; gpio_writes = 0; notifications = 0; ticks = 0;
    tx_count = 0; rx_count = 0; rx_head = 0; busy_tx_attempts = 0; test_critical_depth = 0;
    MX_FREERTOS_Init(); assert(test_timer.PSC == 23 && test_timer.ARR == UINT32_MAX);
}
static CalclptData Acquire(unsigned int count, uint32_t now)
{
    CalclptData sample; test_timer.CNT = now; acquisitions_left = count;
    if (setjmp(acquisition_done) == 0) Task_Control(NULL);
    assert(test_critical_depth == 0);
    assert(xQueueReceive(DataHandle, &sample, 0) == pdTRUE); return sample;
}
static HostControlCommand Command(uint8_t action, uint32_t sequence, uint32_t received)
{
    HostControlCommand command = {0};
    command.action = action; command.command_id = 0x1234;
    command.sample_sequence = sequence; command.received_us = received; return command;
}
static void TestAcquisitionAndWireCompatibility(void)
{
    ResetFixture(); CalclptData sample = Acquire(1, 100000);
    assert(gpio_writes == 0); /* Large forces cannot autonomously change PA4. */
    assert(sample.legacy_values[0] == -20 && sample.legacy_values[1] == 20);
    assert(sample.legacy_values[2] == 8340 && sample.legacy_values[9] == 8348);
    assert(sample.legacy_values[5] == 1070 && sample.legacy_values[6] == 1040);
    assert(sample.legacy_values[12] == 1071 && sample.legacy_values[13] == 1041);
    assert(sample.legacy_values[14] == 8340 && sample.legacy_values[15] == 8348);
    SendSample(&sample); assert(tx_count == 2 && tx_frames[0].id == 0x123 && tx_frames[1].id == 0x124);
    assert(HostProtocol_GetU16(&tx_frames[0].data[0]) == (uint16_t)-20);
    assert(HostProtocol_GetU32(&tx_frames[0].data[32]) == 0); /* Removed flags. */
    assert(HostProtocol_GetU32(&tx_frames[0].data[40]) == 1);
    assert(HostProtocol_GetU32(&tx_frames[0].data[44]) == 100000);
    assert(HostProtocol_GetU16(&tx_frames[1].data[0]) == 2010);
    assert(HostProtocol_GetU32(&tx_frames[1].data[16]) == 1);
}
static void TestProximityChannelsStayIndependentFromForceCin11(void)
{
    ResetFixture();
    /* Current 10-stage wiring: CIN9 = Stage7, CIN4 = Stage4, CIN11 = Stage9.
       Use distinct baselines and signs so a Stage9/Stage7 mix-up cannot pass. */
    adc_values[0][7] = 2100; offset1[7] = 2000; /* JJJ1_1 = +100. */
    adc_values[0][4] = 1700; offset1[4] = 1800; /* JJJ1_2 = -100. */
    adc_values[0][9] = 3500; offset1[9] = 2000;
    adc_values[1][7] = 2300; offset2[7] = 2600; /* JJJ2_1 = -300. */
    adc_values[1][4] = 2200; offset2[4] = 1800; /* JJJ2_2 = +400. */
    adc_values[1][9] = 3900; offset2[9] = 2000;
    CalclptData before = Acquire(1, 100000);
    assert(before.legacy_values[5] == 100 && before.legacy_values[6] == -100);
    assert(before.legacy_values[12] == -300 && before.legacy_values[13] == 400);
    SendSample(&before);
    assert(tx_frames[0].id == 0x123);
    assert(HostProtocol_GetU16(&tx_frames[0].data[10]) == (uint16_t)100);
    assert(HostProtocol_GetU16(&tx_frames[0].data[12]) == (uint16_t)-100);
    assert(HostProtocol_GetU16(&tx_frames[0].data[24]) == (uint16_t)-300);
    assert(HostProtocol_GetU16(&tx_frames[0].data[26]) == (uint16_t)400);

    /* Changing only the force CIN11 must not change any proximity field. */
    adc_values[0][9] += 500;
    adc_values[1][9] -= 200;
    CalclptData after = Acquire(1, 116000);
    const unsigned int proximity_indices[] = {5, 6, 12, 13};
    for (unsigned int i = 0; i < 4; ++i) {
        unsigned int index = proximity_indices[i];
        assert(after.legacy_values[index] == before.legacy_values[index]);
    }
    assert(after.legacy_values[2] == before.legacy_values[2] + 500);
    assert(after.legacy_values[9] == before.legacy_values[9] - 200);
    assert(gpio_writes == 0);
}
static void TestTimedActionAndWrap(void)
{
    ResetFixture(); CalclptData sample = Acquire(1, 100000);
    HostControlCommand command = Command(HOST_ACTION_HIGH, sample.sequence, 105000);
    test_timer.CNT = 106000; ProcessHostCommand(&command);
    const uint8_t *ack = tx_frames[0].data;
    assert(gpio_level == 1 && gpio_writes == 1 && tx_frames[0].id == 0x125);
    assert(ack[0] == 1 && ack[1] == 0 && ack[2] == 1 && ack[3] == 1);
    assert(HostProtocol_GetU16(&ack[4]) == 0x1234 && HostProtocol_GetU16(&ack[6]) == 19);
    assert(HostProtocol_GetU32(&ack[12]) == 100000 && HostProtocol_GetU32(&ack[16]) == 105000);
    assert(HostProtocol_GetU32(&ack[20]) == 106000 && HostProtocol_GetU32(&ack[24]) == 5000);
    ProcessHostCommand(&command);
    assert((HostProtocol_GetU16(&tx_frames[1].data[6]) & HOST_ACK_FLAG_GPIO_CHANGED) == 0);
    ResetFixture(); sample = Acquire(1, 0xFFFFFF00U);
    command = Command(HOST_ACTION_LOW, sample.sequence, 0x100U);
    test_timer.CNT = 0x200U; ProcessHostCommand(&command);
    assert(HostProtocol_GetU32(&tx_frames[0].data[24]) == 512);
}
static void TestZeroAndRejectedReferences(void)
{
    ResetFixture(); CalclptData old = Acquire(2, 100000);
    HostControlCommand command;
    const uint8_t legacy_zero[8] = {0xA4, 2, 1, 0, 0, 0, 0, 0xB7};
    assert(HostProtocol_DecodeCommand(legacy_zero, &command));
    command.received_us = 120000; ProcessHostCommand(&command);
    assert(baseline_reset_request && gpio_writes == 0 && emag_mode == EMAG_MODE_HOLD);
    assert(HostProtocol_GetU16(&tx_frames[0].data[6]) ==
           (HOST_ACK_FLAG_LEGACY | HOST_ACK_FLAG_ZERO_PENDING));
    CalclptData zeroed = Acquire(1, 130000);
    assert(baseline_generation == 1 && !baseline_reset_request);
    for (unsigned int i = 0; i < 16; ++i) assert(zeroed.legacy_values[i] == 0);
    assert(gpio_writes == 0 && gpio_level == 0);
    command = Command(HOST_ACTION_HIGH, old.sequence, 140000); ProcessHostCommand(&command);
    assert(tx_frames[1].data[1] == HOST_ACK_BASELINE_CHANGED && gpio_writes == 0);
    command = Command(HOST_ACTION_HIGH, 999, 140001); ProcessHostCommand(&command);
    assert(tx_frames[2].data[1] == HOST_ACK_UNKNOWN_SAMPLE && gpio_writes == 0);
    ResetFixture(); Acquire(129, 1000);
    command = Command(HOST_ACTION_HIGH, 1, 3000000); ProcessHostCommand(&command);
    assert(tx_frames[0].data[1] == HOST_ACK_UNKNOWN_SAMPLE && gpio_writes == 0);
}
static void TestDecodeAndRxFiltering(void)
{
    ResetFixture(); HostControlCommand command;
    const uint8_t timed[8] = {0xC1, 0, 0x34, 0x12, 0x78, 0x56, 0x34, 0x12};
    assert(HostProtocol_DecodeCommand(timed, &command));
    assert(command.command_id == 0x1234 && command.sample_sequence == 0x12345678 && !command.legacy);
    uint8_t bad[8] = {0xC1, 4}; assert(!HostProtocol_DecodeCommand(bad, &command));
    bad[0] = 0x33; bad[1] = 0; assert(!HostProtocol_DecodeCommand(bad, &command));
    for (unsigned int i = 0; i < 12; ++i) {
        rx_frames[i].header = (FDCAN_RxHeaderTypeDef){FDCAN_EXTENDED_ID, HOST_COMMAND_CAN_ID, FDCAN_DATA_FRAME, FDCAN_DLC_BYTES_8};
        memcpy(rx_frames[i].data, timed, 8);
    }
    rx_frames[0].header.Identifier = 0x123;
    rx_frames[1].header.DataLength = FDCAN_DLC_BYTES_64;
    rx_frames[2].header.RxFrameType = FDCAN_REMOTE_FRAME;
    rx_count = 12; test_timer.CNT = 987654;
    FDCAN_HandleTypeDef handle = {FDCAN1};
    HAL_FDCAN_RxFifo0Callback(&handle, FDCAN_IT_RX_FIFO0_NEW_MESSAGE);
    assert(rx_head == 12 && gpio_writes == 0 && notifications == 8 && dropped_commands == 1);
    assert(xQueueReceive(command_queue, &command, 0));
    assert(command.received_us == 987654 && command.command_id == 0x1234);
}
static void TestBusyTxIsBounded(void)
{
    ResetFixture(); uint8_t data[32] = {0}; busy_tx_attempts = 100;
    SendFrame(0x125, data, FDCAN_DLC_BYTES_32);
    assert(ticks == 5 && dropped_tx_frames == 1 && tx_count == 0);
    busy_tx_attempts = 2; SendFrame(0x125, data, FDCAN_DLC_BYTES_32);
    assert(tx_count == 1 && dropped_tx_frames == 1);
}
int main(void)
{
    TestAcquisitionAndWireCompatibility();
    TestProximityChannelsStayIndependentFromForceCin11();
    TestTimedActionAndWrap();
    TestZeroAndRejectedReferences();
    TestDecodeAndRxFiltering();
    TestBusyTxIsBounded();
    puts("PASS: acquisition compatibility, CIN9/CIN4 proximity mapping and isolation from CIN11, no autonomous output, timing/rollover, zero/hold, stale references, RX validation/overflow, bounded TX retries");
    return 0;
}
