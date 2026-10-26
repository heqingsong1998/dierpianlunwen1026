#ifndef  __CAN_H
#define __CAN_H


#include "main.h"

void FDCAN1_Config(void);
HAL_StatusTypeDef canfd_send_bytes(uint16_t canid, const uint8_t *data, uint32_t dlc);
void canfd_tx_test(uint16_t canid,int16_t *data);
void canfd_8b_test(uint16_t canid,int16_t *data);
void canfd_48b_test(uint16_t canid,int16_t *data);
#endif // ! __AutoOffset_H
