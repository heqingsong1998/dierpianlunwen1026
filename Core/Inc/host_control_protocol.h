#ifndef HOST_CONTROL_PROTOCOL_H
#define HOST_CONTROL_PROTOCOL_H

#include <stdint.h>

/* All multibyte wire fields are little endian. See docs/host_control_protocol.md. */
#define HOST_PROTOCOL_VERSION       1U
#define HOST_COMMAND_CAN_ID         0xFFFEUL
#define HOST_DATA_CAN_ID            0x123U
#define HOST_STAGE_PAIR_CAN_ID      0x124U
#define HOST_ACK_CAN_ID             0x125U
#define HOST_COMMAND_MAGIC          0xC1U
#define HOST_ACK_SIZE               32U
#define HOST_TIME_INVALID           0xFFFFFFFFUL

#define HOST_ACTION_LOW             0U
#define HOST_ACTION_HIGH            1U
#define HOST_ACTION_HOLD            2U
#define HOST_ACTION_ZERO            3U

#define HOST_ACK_OK                 0U
#define HOST_ACK_UNKNOWN_SAMPLE     1U
#define HOST_ACK_BASELINE_CHANGED   2U

#define HOST_ACK_FLAG_TIMING_VALID  0x0001U
#define HOST_ACK_FLAG_GPIO_WRITTEN  0x0002U
#define HOST_ACK_FLAG_ZERO_PENDING  0x0004U
#define HOST_ACK_FLAG_LEGACY        0x0008U
#define HOST_ACK_FLAG_GPIO_CHANGED  0x0010U

typedef struct {
    uint32_t sample_sequence;
    uint32_t received_us;
    uint16_t command_id;
    uint8_t action;
    uint8_t zero_requested;
    uint8_t legacy;
} HostControlCommand;

typedef struct {
    uint32_t sample_sequence;
    uint32_t ready_us;
    uint32_t received_us;
    uint32_t gpio_written_us;
    uint32_t round_trip_us;
    uint16_t command_id;
    uint16_t flags;
    uint16_t baseline_generation;
    uint16_t dropped_commands;
    uint8_t status;
    uint8_t action;
    uint8_t pin_state;
} HostControlAck;

static inline uint16_t HostProtocol_GetU16(const uint8_t *data)
{
    return (uint16_t)((uint16_t)data[0] | ((uint16_t)data[1] << 8));
}

static inline uint32_t HostProtocol_GetU32(const uint8_t *data)
{
    return (uint32_t)data[0] | ((uint32_t)data[1] << 8) |
           ((uint32_t)data[2] << 16) | ((uint32_t)data[3] << 24);
}

static inline void HostProtocol_PutU16(uint8_t *data, uint16_t value)
{
    data[0] = (uint8_t)value;
    data[1] = (uint8_t)(value >> 8);
}

static inline void HostProtocol_PutU32(uint8_t *data, uint32_t value)
{
    data[0] = (uint8_t)value;
    data[1] = (uint8_t)(value >> 8);
    data[2] = (uint8_t)(value >> 16);
    data[3] = (uint8_t)(value >> 24);
}

/* The caller validates CAN ID, frame type and the exact eight-byte DLC first. */
static inline uint8_t HostProtocol_DecodeCommand(const uint8_t *data,
                                                HostControlCommand *command)
{
    HostControlCommand decoded = {0};

    if (data[0] == HOST_COMMAND_MAGIC) {
        if (data[1] > HOST_ACTION_ZERO) {
            return 0U;
        }
        decoded.action = data[1];
        decoded.command_id = HostProtocol_GetU16(&data[2]);
        decoded.sample_sequence = HostProtocol_GetU32(&data[4]);
        decoded.zero_requested = (data[1] == HOST_ACTION_ZERO) ? 1U : 0U;
    } else if ((data[0] == 0xA4U || data[0] == 0xA5U) &&
               data[1] <= HOST_ACTION_HOLD && data[2] <= 1U) {
        /* Legacy mode 2 now means HOLD; it never enables a local threshold. */
        decoded.action = data[1];
        decoded.zero_requested = data[2];
        decoded.legacy = 1U;
    } else {
        return 0U;
    }

    *command = decoded;
    return 1U;
}

static inline void HostProtocol_EncodeAck(uint8_t *data, const HostControlAck *ack)
{
    data[0] = HOST_PROTOCOL_VERSION;
    data[1] = ack->status;
    data[2] = ack->action;
    data[3] = ack->pin_state;
    HostProtocol_PutU16(&data[4], ack->command_id);
    HostProtocol_PutU16(&data[6], ack->flags);
    HostProtocol_PutU32(&data[8], ack->sample_sequence);
    HostProtocol_PutU32(&data[12], ack->ready_us);
    HostProtocol_PutU32(&data[16], ack->received_us);
    HostProtocol_PutU32(&data[20], ack->gpio_written_us);
    HostProtocol_PutU32(&data[24], ack->round_trip_us);
    HostProtocol_PutU16(&data[28], ack->baseline_generation);
    HostProtocol_PutU16(&data[30], ack->dropped_commands);
}

#endif
