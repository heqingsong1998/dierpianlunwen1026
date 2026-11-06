#ifndef FOOT_CONTROLLER_H
#define FOOT_CONTROLLER_H

#include <stdint.h>

#define FOOT_THRESHOLD 1U
#define FOOT_PREDICTION 2U
#define FOOT_DISARMED 0U
#define FOOT_ARMED 1U
#define FOOT_TRIGGERED 2U
#define FOOT_WARMUP 3U
#define FOOT_DEADLINE 4U
#define FOOT_WAIT_CLEAR 5U

typedef struct {
    float calibration[3];
    float threshold;
    uint8_t strategy;
    uint8_t output;
} FootConfig;

typedef struct {
    FootConfig config;
    float history[16][5];
    uint32_t last_ready_us;
    uint8_t count;
    uint8_t next;
} FootController;

void Foot_Init(FootController *controller);
uint32_t Foot_ModelTag(void);
uint8_t Foot_MatchesModel(const float *calibration);
void Foot_ResetHistory(FootController *controller);
float Foot_Sample(FootController *controller, const int16_t *raw, uint32_t ready_us);
void Foot_Predict(const FootController *controller, float result[16]);
float Foot_Crossing(const float result[16], float threshold);

#endif
