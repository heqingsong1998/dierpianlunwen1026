#include "foot_controller.h"
#include <string.h>

typedef struct {
    float mean[5];
    float std[5];
    float input_weights[184 * 40];
    float input_bias[184];
    float output_weights[16 * 184];
    float output_bias[16];
} FootModel;

#include "foot_model_weights.inc"

void Foot_Init(FootController *controller)
{
    memset(controller, 0, sizeof(*controller));
    memcpy(controller->config.calibration, model_calibration, sizeof(model_calibration));
    controller->config.threshold = 3.0f;
    controller->config.strategy = FOOT_THRESHOLD;
    controller->config.output = 1U;
}

uint32_t Foot_ModelTag(void)
{
    return FOOT_MODEL_TAG;
}

uint8_t Foot_MatchesModel(const float *calibration)
{
    return memcmp(calibration, model_calibration, sizeof(model_calibration)) == 0;
}

void Foot_ResetHistory(FootController *controller)
{
    controller->count = 0;
    controller->next = 0;
}

float Foot_Sample(FootController *controller, const int16_t *raw, uint32_t ready_us)
{
    const float *cal = controller->config.calibration;
    float force = -(cal[0] * raw[2] + cal[1] * raw[9] + cal[2]);
    float *frame;
    uint32_t elapsed = ready_us - controller->last_ready_us;

    if (elapsed < 12000U || elapsed > 20000U) {
        Foot_ResetHistory(controller);
    }
    controller->last_ready_us = ready_us;
    frame = controller->history[controller->next];
    frame[0] = force;
    frame[1] = raw[5];
    frame[2] = raw[6];
    frame[3] = raw[12];
    frame[4] = raw[13];
    controller->next = (controller->next + 1U) & 15U;
    if (controller->count < 16U) {
        controller->count++;
    }
    return force;
}

static float Dot(const float *weights, const float *values, unsigned int size, float sum)
{
    float sum1 = 0.0f;
    float sum2 = 0.0f;
    float sum3 = 0.0f;
    const float *end = weights + size;
    for (; weights < end; weights += 4U, values += 4U) {
        sum += weights[0] * values[0];
        sum1 += weights[1] * values[1];
        sum2 += weights[2] * values[2];
        sum3 += weights[3] * values[3];
    }
    return (sum + sum1) + (sum2 + sum3);
}

void Foot_Predict(const FootController *controller, float result[16])
{
    static const unsigned int scales[4] = {2U, 4U, 8U, 16U};
    float standardized[16][5];
    float features[40];
    float hidden[184];
    const FootModel *model = &foot_model;

    for (unsigned int channel = 0; channel < 5U; ++channel) {
        float inverse_std = 1.0f / model->std[channel];
        for (unsigned int i = 0; i < 16U; ++i) {
            float value = controller->history[(controller->next + i) & 15U][channel];
            standardized[i][channel] = (value - model->mean[channel]) * inverse_std;
        }
    }
    for (unsigned int scale = 0; scale < 4U; ++scale) {
        unsigned int count = scales[scale];
        unsigned int first = 16U - count;
        for (unsigned int channel = 0; channel < 5U; ++channel) {
            float sum = 0.0f;
            for (unsigned int i = first; i < 16U; ++i) {
                sum += standardized[i][channel];
            }
            features[10U * scale + channel] = sum / (float)count;
            features[10U * scale + 5U + channel] =
                (standardized[15][channel] - standardized[first][channel]) /
                (float)(16U * (count - 1U));
        }
    }
    for (unsigned int i = 0; i < 184U; ++i) {
        float value = Dot(&model->input_weights[i * 40U], features, 40U, model->input_bias[i]);
        hidden[i] = value > 0.0f ? value : 0.0f;
    }
    for (unsigned int i = 0; i < 16U; ++i) {
        result[i] = Dot(&model->output_weights[i * 184U], hidden, 184U, model->output_bias[i]);
    }
}

float Foot_Crossing(const float result[16], float threshold)
{
    if (result[0] >= threshold) {
        return 0.0f;
    }
    for (unsigned int i = 1; i < 16U; ++i) {
        if (result[i] >= threshold) {
            return (float)(i - 1U) + (threshold - result[i - 1U]) / (result[i] - result[i - 1U]);
        }
    }
    return -1.0f;
}
