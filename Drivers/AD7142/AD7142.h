#include "stm32g4xx_hal.h"
#include "Bit_IO.h"

#define AD7142_PROTOCOL 1 //1 for IIC,0 for SPI 

#ifndef __AD7142_H
#define __AD7142_H

#define ToSRC 0
#define ToDCS 1

#define SW1_4_ToSRC LL_GPIO_ResetOutputPin(GPIOB, LL_GPIO_PIN_5)
#define SW1_3_ToSRC LL_GPIO_ResetOutputPin(GPIOB, LL_GPIO_PIN_4)
#define SW1_2_ToSRC LL_GPIO_ResetOutputPin(GPIOB, LL_GPIO_PIN_3)
#define SW1_1_ToSRC LL_GPIO_ResetOutputPin(GPIOA, LL_GPIO_PIN_15)
#define SW2_4_ToSRC LL_GPIO_ResetOutputPin(GPIOB, LL_GPIO_PIN_2)
#define SW2_3_ToSRC LL_GPIO_ResetOutputPin(GPIOB, LL_GPIO_PIN_0)
#define SW2_2_ToSRC LL_GPIO_ResetOutputPin(GPIOA, LL_GPIO_PIN_6)
#define SW2_1_ToSRC LL_GPIO_ResetOutputPin(GPIOA, LL_GPIO_PIN_4)

#define SW1_4_ToDCS LL_GPIO_SetOutputPin(GPIOB, LL_GPIO_PIN_5)
#define SW1_3_ToDCS LL_GPIO_SetOutputPin(GPIOB, LL_GPIO_PIN_4)
#define SW1_2_ToDCS LL_GPIO_SetOutputPin(GPIOB, LL_GPIO_PIN_3)
#define SW1_1_ToDCS LL_GPIO_SetOutputPin(GPIOA, LL_GPIO_PIN_15)
#define SW2_4_ToDCS LL_GPIO_SetOutputPin(GPIOB, LL_GPIO_PIN_2)
#define SW2_3_ToDCS LL_GPIO_SetOutputPin(GPIOB, LL_GPIO_PIN_0)
#define SW2_2_ToDCS LL_GPIO_SetOutputPin(GPIOA, LL_GPIO_PIN_6)
#define SW2_1_ToDCS LL_GPIO_SetOutputPin(GPIOA, LL_GPIO_PIN_4)


// Bank1: 全局可读写寄存器
#define PWR_CONTROL										0x000
#define STAGE_CAL_EN									0x001 // stage校准使能寄存器，本工程初始化时写0x0000
#define AMB_COMP_CTRL0								0x002 // 环境补偿控制寄存器0
#define AMB_COMP_CTRL1								0x003 // 环境补偿控制寄存器1
#define AMB_COMP_CTRL2								0x004 // 环境补偿控制寄存器2
#define STAGE_LOW_INT_ENABLE					0x005 // 各stage低阈值中断使能寄存器
#define STAGE_HIGH_INT_ENABLE 				0x006 // 各stage高阈值中断使能寄存器
#define STAGE_COMPLETE_INT_EN			 		0x007

#define STAGE0_SETTING_REG						0x080
#define STAGE1_SETTING_REG						0x088
#define STAGE2_SETTING_REG						0x090
#define STAGE3_SETTING_REG						0x098
#define STAGE4_SETTING_REG						0x0A0
#define STAGE5_SETTING_REG						0x0A8
#define STAGE6_SETTING_REG						0x0B0
#define STAGE7_SETTING_REG						0x0B8
#define STAGE8_SETTING_REG						0x0C0
#define STAGE9_SETTING_REG						0x0C8
#define STAGE10_SETTING_REG						0x0D0
#define STAGE11_SETTING_REG						0x0D8
#define STAGEx_CONNECTION_6_0_offset 	0x00
#define STAGEx_CONNECTION_12_7_offset	0x01
#define STAGEx_AFE_OFFSET_offset 			0x02
#define STAGEx_SENSITIVITY_offset 		0x03
#define STAGEx_OFFSET_LOW_offset 			0x04
#define STAGEx_OFFSET_HIGH_offset 		0x05
#define STAGEx_OFFSET_LOW_CLAMP_offset 	0x06
#define STAGEx_OFFSET_HIGH_CLAMP_offset 0x07


// Bank1: 只读状态/结果寄存器
#define STAGE_COMPLETE_INT_STATUS 0x00A  // 读取后清除stage转换完成中断状态位
#define CDC_RESULT_S0 0x00B
#define CDC_RESULT_S1 0x00C
#define CDC_RESULT_S2 0x00D
#define CDC_RESULT_S3 0x00E
#define CDC_RESULT_S4 0x00F
#define CDC_RESULT_S5 0x010
#define CDC_RESULT_S6 0x011
#define CDC_RESULT_S7 0x012
#define CDC_RESULT_S8 0x013
#define CDC_RESULT_S9 0x014
#define CDC_RESULT_S10 	0x015
#define CDC_RESULT_S11 	0x016
#define AD7142_Device_ID	0x017


#if AD7142_PROTOCOL  //IIC

	#define software_iic_speed 3   //0最慢3最快
	
	#define AD7142_IIC_Addr1 0x2C<<1 // I2C模式下7位器件地址需左移1位
	#define AD7142_IIC_Addr2 0x2D<<1
	
	#define AD7142_SCL_Pin 	LL_GPIO_PIN_15
	#define AD7142_SCL_Port	GPIOA
	#define AD7142_SDA_Pin	LL_GPIO_PIN_7
	#define AD7142_SDA_Port	GPIOB
	
	#define AD7142_SCL_Pin2 	LL_GPIO_PIN_9
	#define AD7142_SCL_Port2	GPIOA
	#define AD7142_SDA_Pin2	LL_GPIO_PIN_8
	#define AD7142_SDA_Port2	GPIOA
	#else							//SPI
	
		#define AD7142_READ 0xE400
		#define AD7142_WRITE 0xE000
		
		#define AD7142_CS1_ENABLE 	LL_GPIO_ResetOutputPin(GPIOB, LL_GPIO_PIN_6)
		#define AD7142_CS1_DISABLE 	LL_GPIO_SetOutputPin(GPIOB, LL_GPIO_PIN_6)

		#define AD7142_CS2_ENABLE 	LL_GPIO_ResetOutputPin(GPIOB, LL_GPIO_PIN_8)
		#define AD7142_CS2_DISABLE 	LL_GPIO_SetOutputPin(GPIOB, LL_GPIO_PIN_8)
#endif
	
void AD7142_Protocol_init(void);
void AD7142_Protocol_init2(void);
void AD7142_init(void);
void AD7142_ExtInit_init(void);
void AD7142_Write1(uint16_t addr,uint16_t data);
uint16_t AD7142_Read1(uint16_t addr,uint16_t* result,uint16_t size);
void AD7142_Write2(uint16_t addr,uint16_t data);
uint16_t AD7142_Read2(uint16_t addr,uint16_t* result,uint16_t size);
#endif
