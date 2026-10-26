#include "can.h"
#include "main.h"
#include "fdcan.h"

FDCAN_TxHeaderTypeDef TxHeader1;
FDCAN_RxHeaderTypeDef RxHeader1;

void FDCAN1_Config(void)
{
  FDCAN_FilterTypeDef sFilterConfig;
  /* Configure Rx filter */
  sFilterConfig.IdType = FDCAN_STANDARD_ID;
  sFilterConfig.FilterIndex = 1;
  sFilterConfig.FilterType = FDCAN_FILTER_RANGE;
  sFilterConfig.FilterConfig = FDCAN_FILTER_TO_RXFIFO0;
  sFilterConfig.FilterID1 = 0x00000000;
  sFilterConfig.FilterID2 = 0x000007FF;
  if (HAL_FDCAN_ConfigFilter(&hfdcan1, &sFilterConfig) != HAL_OK)
  {
    Error_Handler();
  }

  sFilterConfig.IdType = FDCAN_EXTENDED_ID;
  sFilterConfig.FilterIndex = 0;
  sFilterConfig.FilterType = FDCAN_FILTER_RANGE;
  sFilterConfig.FilterConfig = FDCAN_FILTER_TO_RXFIFO0;
  sFilterConfig.FilterID1 = 0x00000000;
  sFilterConfig.FilterID2 = 0x1FFFFFFF;
  if (HAL_FDCAN_ConfigFilter(&hfdcan1, &sFilterConfig) != HAL_OK)
  {
    Error_Handler();
  }

  /* Configure global filter:
     Filter all remote frames with STD and EXT ID
     Reject non matching frames with STD ID and EXT ID */
  if (HAL_FDCAN_ConfigGlobalFilter(&hfdcan1, FDCAN_REJECT, FDCAN_REJECT, FDCAN_FILTER_REMOTE, FDCAN_FILTER_REMOTE) != HAL_OK)
  {
    Error_Handler();
  }

  /* Activate Rx FIFO 0 new message notification on both FDCAN instances */
  if (HAL_FDCAN_ActivateNotification(&hfdcan1, FDCAN_IT_RX_FIFO0_NEW_MESSAGE, 0) != HAL_OK)
  {
    Error_Handler();
  }

  // if (HAL_FDCAN_ActivateNotification(&hfdcan1, FDCAN_IT_BUS_OFF, 0) != HAL_OK)
  // {
  //   Error_Handler();
  // }

  /* Tx Config*/
  TxHeader1.Identifier = 0x000000000;                 // CAN ID
  TxHeader1.IdType = FDCAN_STANDARD_ID;               // ±ê×¼ID
  TxHeader1.TxFrameType = FDCAN_DATA_FRAME;           
  TxHeader1.DataLength = FDCAN_DLC_BYTES_8;
  TxHeader1.ErrorStateIndicator = FDCAN_ESI_PASSIVE;
  TxHeader1.BitRateSwitch = FDCAN_BRS_OFF;
  TxHeader1.FDFormat = FDCAN_FD_CAN;                  // CANFD
  TxHeader1.TxEventFifoControl = FDCAN_NO_TX_EVENTS;  
  TxHeader1.MessageMarker = 0;

  /* Configure and enable Tx Delay Compensation, required for BRS mode.
        TdcOffset default recommended value: DataTimeSeg1 * DataPrescaler
        TdcFilter default recommended value: 0 */
  HAL_FDCAN_ConfigTxDelayCompensation(&hfdcan1, hfdcan1.Init.DataPrescaler * hfdcan1.Init.DataTimeSeg1, 0);
  HAL_FDCAN_EnableTxDelayCompensation(&hfdcan1);
  
  /* Start the FDCAN module */
  if (HAL_FDCAN_Start(&hfdcan1) != HAL_OK)
  {
    Error_Handler();
  }
}

HAL_StatusTypeDef canfd_send_bytes(uint16_t canid, const uint8_t *data, uint32_t dlc)
{
  FDCAN_TxHeaderTypeDef header = {0};

  if (data == NULL || canid > 0x7FFU ||
      (dlc != FDCAN_DLC_BYTES_8 && dlc != FDCAN_DLC_BYTES_32 &&
       dlc != FDCAN_DLC_BYTES_48)) {
    return HAL_ERROR;
  }
  if (HAL_FDCAN_GetTxFifoFreeLevel(&hfdcan1) == 0U) {
    return HAL_BUSY;
  }

  header.Identifier = canid;
  header.IdType = FDCAN_STANDARD_ID;
  header.TxFrameType = FDCAN_DATA_FRAME;
  header.DataLength = dlc;
  header.ErrorStateIndicator = FDCAN_ESI_PASSIVE;
  header.BitRateSwitch = FDCAN_BRS_OFF;
  header.FDFormat = FDCAN_FD_CAN;
  header.TxEventFifoControl = FDCAN_NO_TX_EVENTS;
  header.MessageMarker = 0U;
  return HAL_FDCAN_AddMessageToTxFifoQ(&hfdcan1, &header, (uint8_t *)data);
}

/* Compatibility wrappers. Task_CAN uses the status-returning function above. */
void canfd_tx_test(uint16_t canid, int16_t *data)
{
  (void)canfd_send_bytes(canid, (const uint8_t *)data, FDCAN_DLC_BYTES_32);
}

void canfd_8b_test(uint16_t canid, int16_t *data)
{
  (void)canfd_send_bytes(canid, (const uint8_t *)data, FDCAN_DLC_BYTES_8);
}

void canfd_48b_test(uint16_t canid, int16_t *data)
{
  (void)canfd_send_bytes(canid, (const uint8_t *)data, FDCAN_DLC_BYTES_48);
}
