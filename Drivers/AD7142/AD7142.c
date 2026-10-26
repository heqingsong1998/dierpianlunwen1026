#include "AD7142.h"
#include "stm32g4xx_ll_gpio.h"
#include "main.h"

unsigned char MRD[2]={0,0};
#if AD7142_PROTOCOL		//IIC
#if software_iic_speed==3		//大约1.2Mbps
	#define delay_cycle 2
	#define start_stop_cycle 2
#endif
#if software_iic_speed==2		//大约1Mbps
	#define delay_cycle 3
	#define start_stop_cycle 3
#endif
#if software_iic_speed==1		//大约400k
	#define delay_cycle 12
	#define start_stop_cycle 6
#endif
#if software_iic_speed==0		//大约100k
	#define delay_cycle 51
	#define start_stop_cycle 25
#endif

void AD7142_Protocol_init(void){  //初始化IO   
	LL_GPIO_InitTypeDef GPIO_InitStruct = {0};  
  LL_AHB2_GRP1_EnableClock(LL_AHB2_GRP1_PERIPH_GPIOA);
  LL_AHB2_GRP1_EnableClock(LL_AHB2_GRP1_PERIPH_GPIOB);

	LL_GPIO_SetOutputPin(AD7142_SCL_Port, AD7142_SCL_Pin);
	LL_GPIO_SetOutputPin(AD7142_SDA_Port, AD7142_SDA_Pin);
	
	GPIO_InitStruct.Pin = AD7142_SCL_Pin;
  GPIO_InitStruct.Mode = LL_GPIO_MODE_OUTPUT;
  GPIO_InitStruct.Speed = LL_GPIO_SPEED_FREQ_HIGH;
  GPIO_InitStruct.OutputType = LL_GPIO_OUTPUT_PUSHPULL;
  LL_GPIO_Init(AD7142_SCL_Port, &GPIO_InitStruct);
	
	GPIO_InitStruct.Pin = AD7142_SDA_Pin;
  LL_GPIO_Init(AD7142_SDA_Port, &GPIO_InitStruct);
	
	HAL_Delay(1);
}

void IIC_Start(void){           //产生IIC起始信号
	uint8_t a;
	LL_GPIO_SetPinMode(AD7142_SDA_Port, AD7142_SDA_Pin, LL_GPIO_MODE_OUTPUT);     //sda线输出
	LL_GPIO_SetOutputPin(AD7142_SDA_Port, AD7142_SDA_Pin); 	
	LL_GPIO_SetOutputPin(AD7142_SCL_Port, AD7142_SCL_Pin);
	a = delay_cycle;while(a--){__NOP();}  
 	LL_GPIO_ResetOutputPin(AD7142_SDA_Port, AD7142_SDA_Pin);    //START:when CLK is high,DATA change form high to low 
	a = start_stop_cycle;while(a--){__NOP();}
	LL_GPIO_ResetOutputPin(AD7142_SCL_Port, AD7142_SCL_Pin);    //钳住I2C总线，准备发送或接收数据 
}	  

void IIC_Stop(void){            //产生IIC停止信号
	uint8_t a;
	LL_GPIO_SetPinMode(AD7142_SDA_Port, AD7142_SDA_Pin, LL_GPIO_MODE_OUTPUT);  //sda线输出
	LL_GPIO_ResetOutputPin(AD7142_SDA_Port, AD7142_SDA_Pin);  //STOP:when CLK is high DATA change form low to high
	//LL_GPIO_ResetOutputPin(AD7142_SCL_Port, AD7142_SCL_Pin);
	a = delay_cycle;while(a--){__NOP();} 
	LL_GPIO_SetOutputPin(AD7142_SCL_Port, AD7142_SCL_Pin);  //
	a = start_stop_cycle;while(a--){__NOP();}
	LL_GPIO_SetOutputPin(AD7142_SDA_Port, AD7142_SDA_Pin);//发送I2C总线结束信号   	
}
uint8_t IIC_Wait_Ack(void){		//等待应答信号到来//返回值：0，接收应答失败 1成功
	uint8_t ErrTime=0;
	LL_GPIO_SetPinMode(AD7142_SDA_Port, AD7142_SDA_Pin, LL_GPIO_MODE_INPUT);     //SDA设置为输入  
	//LL_GPIO_SetOutputPin(AD7142_SDA_Port, AD7142_SDA_Pin);
	//uint8_t a = start_stop_cycle;while(a--){__NOP();} //等待ACKS，SCL第九个上升沿的延迟时间
	LL_GPIO_SetOutputPin(AD7142_SCL_Port, AD7142_SCL_Pin);
	while(LL_GPIO_IsInputPinSet(AD7142_SDA_Port, AD7142_SDA_Pin))
	{
		ErrTime++;
		if(ErrTime>250)
		{
			IIC_Stop();
			return 0;
		}
	}
	LL_GPIO_ResetOutputPin(AD7142_SCL_Port, AD7142_SCL_Pin);//时钟输出0 	   
	return 1;  
} 

void IIC_Ack(void){             //发送ACKM应答
	uint8_t a=0;
	LL_GPIO_SetPinMode(AD7142_SDA_Port, AD7142_SDA_Pin, LL_GPIO_MODE_OUTPUT);
	LL_GPIO_ResetOutputPin(AD7142_SDA_Port, AD7142_SDA_Pin);
	a = delay_cycle;while(a--){__NOP();}
	LL_GPIO_SetOutputPin(AD7142_SCL_Port, AD7142_SCL_Pin);
	a = delay_cycle;while(a--){__NOP();}
	LL_GPIO_ResetOutputPin(AD7142_SCL_Port, AD7142_SCL_Pin);
}
	    
void IIC_NAck(void){            //发送NACKM应答	
	uint8_t a;
	LL_GPIO_SetPinMode(AD7142_SDA_Port, AD7142_SDA_Pin, LL_GPIO_MODE_OUTPUT);
	LL_GPIO_SetOutputPin(AD7142_SDA_Port, AD7142_SDA_Pin);
	a = delay_cycle;while(a--){__NOP();}
	LL_GPIO_SetOutputPin(AD7142_SCL_Port, AD7142_SCL_Pin);
	a = delay_cycle;while(a--){__NOP();}
	LL_GPIO_ResetOutputPin(AD7142_SCL_Port, AD7142_SCL_Pin);
}					 				     
 
void IIC_Send_Byte(uint8_t txd){     //发送1个字节（只包含8个SCL时钟周期的时序）
	uint8_t a;
	LL_GPIO_SetPinMode(AD7142_SDA_Port, AD7142_SDA_Pin, LL_GPIO_MODE_OUTPUT);
	for(uint8_t t=0; t<8; t++){
		if((txd&0x80)!=0)
			LL_GPIO_SetOutputPin(AD7142_SDA_Port, AD7142_SDA_Pin);
		else
			LL_GPIO_ResetOutputPin(AD7142_SDA_Port, AD7142_SDA_Pin);
	  txd<<=1; 
		a = delay_cycle;while(a--){__NOP();}
		LL_GPIO_SetOutputPin(AD7142_SCL_Port, AD7142_SCL_Pin);
		a = delay_cycle;while(a--){__NOP();}
		LL_GPIO_ResetOutputPin(AD7142_SCL_Port, AD7142_SCL_Pin);
	}	
	IIC_Wait_Ack();
}

uint8_t IIC_Receive_Byte(void){      //接收1个字节（只包含8个SCL时钟周期的时序）
	uint8_t receive=0,a=0;
	LL_GPIO_SetPinMode(AD7142_SDA_Port, AD7142_SDA_Pin, LL_GPIO_MODE_INPUT);//SDA设置为输入
  for(uint8_t i=0; i<7; i++){
		LL_GPIO_SetOutputPin(AD7142_SCL_Port, AD7142_SCL_Pin);
		//a = delay_cycle>>1;while(a--){__NOP();}
		receive += (LL_GPIO_IsInputPinSet(AD7142_SDA_Port, AD7142_SDA_Pin))&0x01;
		LL_GPIO_ResetOutputPin(AD7142_SCL_Port, AD7142_SCL_Pin); 
		receive<<=1;
		//a = delay_cycle>>1;while(a--){__NOP();}
		a = delay_cycle;while(a--){__NOP();}
	}
	LL_GPIO_SetOutputPin(AD7142_SCL_Port, AD7142_SCL_Pin);
	a = delay_cycle>>1;while(a--){__NOP();}
	receive += (LL_GPIO_IsInputPinSet(AD7142_SDA_Port, AD7142_SDA_Pin))&0x01;
	LL_GPIO_ResetOutputPin(AD7142_SCL_Port, AD7142_SCL_Pin); 
	a = delay_cycle>>1;while(a--){__NOP();}
	a = delay_cycle;while(a--){__NOP();}
	return receive;
}

void IIC_Write_Byte8(uint8_t Device_addr, uint8_t Register_addr, uint8_t byte){  //发送一字节数据（完整时序）
	IIC_Start();
	IIC_Send_Byte((Device_addr) | 0x00);
	IIC_Send_Byte(Register_addr);
	IIC_Send_Byte(byte);
	IIC_Stop();
}

uint8_t IIC_Read_Byte8(uint8_t Device_addr, uint8_t Register_addr){              //读取一字节数据（完整时序）
	IIC_Start();
	IIC_Send_Byte((Device_addr) | 0x00);
	IIC_Send_Byte(Register_addr);
	IIC_Start();
	IIC_Send_Byte((Device_addr) | 0x01);
	uint8_t recv_byte = IIC_Receive_Byte();
	IIC_NAck();
	IIC_Stop();
	return recv_byte;
}

void AD7142_Write1(uint16_t addr,uint16_t data){
	IIC_Start();
	IIC_Send_Byte(AD7142_IIC_Addr1 | 0x00);
	IIC_Send_Byte(addr>>8);
	IIC_Send_Byte(addr&0xff);
	IIC_Send_Byte(data>>8);
	IIC_Send_Byte(data&0xff);
	IIC_Stop();	
}

uint16_t AD7142_Read1(uint16_t addr,uint16_t* result,uint16_t size){
	if(size){
		IIC_Start();
		IIC_Send_Byte(AD7142_IIC_Addr1 | 0x00);
		IIC_Send_Byte(addr>>8);
		IIC_Send_Byte(addr&0xff);
		IIC_Start();
		IIC_Send_Byte(AD7142_IIC_Addr1 | 0x01);
		while(--size){
			*result = IIC_Receive_Byte()<<8;
			IIC_Ack();
			*result |= IIC_Receive_Byte();
			IIC_Ack();
			result++;
		}
		*result = IIC_Receive_Byte()<<8;
		IIC_Ack();
		*result |= IIC_Receive_Byte();
		IIC_NAck();
	}
	else{
		IIC_Start();
		IIC_Send_Byte(AD7142_IIC_Addr1 | 0x00);
		IIC_Send_Byte(addr>>8);
		IIC_Send_Byte(addr&0xff);
		IIC_Start();
		IIC_Send_Byte(AD7142_IIC_Addr1 | 0x01);
		IIC_Receive_Byte();
		IIC_Ack();
		IIC_Receive_Byte();
		IIC_NAck();
	}
	IIC_Stop();
	return 1;
}


void AD7142_Protocol_init2(void){  //初始化IO   
	LL_GPIO_InitTypeDef GPIO_InitStruct = {0};  
  LL_AHB2_GRP1_EnableClock(LL_AHB2_GRP1_PERIPH_GPIOA);
  LL_AHB2_GRP1_EnableClock(LL_AHB2_GRP1_PERIPH_GPIOB);

	LL_GPIO_SetOutputPin(AD7142_SCL_Port2, AD7142_SCL_Pin2);
	LL_GPIO_SetOutputPin(AD7142_SDA_Port2, AD7142_SDA_Pin2);
	
	GPIO_InitStruct.Pin = AD7142_SCL_Pin2;
  GPIO_InitStruct.Mode = LL_GPIO_MODE_OUTPUT;
  GPIO_InitStruct.Speed = LL_GPIO_SPEED_FREQ_HIGH;
  GPIO_InitStruct.OutputType = LL_GPIO_OUTPUT_PUSHPULL;
  LL_GPIO_Init(AD7142_SCL_Port2, &GPIO_InitStruct);
	
	GPIO_InitStruct.Pin = AD7142_SDA_Pin2;
  LL_GPIO_Init(AD7142_SDA_Port2, &GPIO_InitStruct);
	
	HAL_Delay(1);
}

void IIC_Start2(void){           //产生IIC起始信号
	uint8_t a;
	LL_GPIO_SetPinMode(AD7142_SDA_Port2, AD7142_SDA_Pin2, LL_GPIO_MODE_OUTPUT);     //sda线输出
	LL_GPIO_SetOutputPin(AD7142_SDA_Port2, AD7142_SDA_Pin2); 	
	LL_GPIO_SetOutputPin(AD7142_SCL_Port2, AD7142_SCL_Pin2);
	a = delay_cycle;while(a--){__NOP();}  
 	LL_GPIO_ResetOutputPin(AD7142_SDA_Port2, AD7142_SDA_Pin2);    //START:when CLK is high,DATA change form high to low 
	a = start_stop_cycle;while(a--){__NOP();}
	LL_GPIO_ResetOutputPin(AD7142_SCL_Port2, AD7142_SCL_Pin2);    //钳住I2C总线，准备发送或接收数据 
}	  

void IIC_Stop2(void){            //产生IIC停止信号
	uint8_t a;
	LL_GPIO_SetPinMode(AD7142_SDA_Port2, AD7142_SDA_Pin2, LL_GPIO_MODE_OUTPUT);  //sda线输出
	LL_GPIO_ResetOutputPin(AD7142_SDA_Port2, AD7142_SDA_Pin2);  //STOP:when CLK is high DATA change form low to high
	//LL_GPIO_ResetOutputPin(AD7142_SCL_Port2, AD7142_SCL_Pin2);
	a = delay_cycle;while(a--){__NOP();} 
	LL_GPIO_SetOutputPin(AD7142_SCL_Port2, AD7142_SCL_Pin2);  //
	a = start_stop_cycle;while(a--){__NOP();}
	LL_GPIO_SetOutputPin(AD7142_SDA_Port2, AD7142_SDA_Pin2);//发送I2C总线结束信号   	
}

uint8_t IIC_Wait_Ack2(void){		//等待应答信号到来//返回值：0，接收应答失败 1成功
	uint8_t ErrTime=0;
	LL_GPIO_SetPinMode(AD7142_SDA_Port2, AD7142_SDA_Pin2, LL_GPIO_MODE_INPUT);     //SDA设置为输入  
	//LL_GPIO_SetOutputPin(AD7142_SDA_Port2, AD7142_SDA_Pin2);
	//uint8_t a = start_stop_cycle;while(a--){__NOP();} //等待ACKS，SCL第九个上升沿的延迟时间
	LL_GPIO_SetOutputPin(AD7142_SCL_Port2, AD7142_SCL_Pin2);
	while(LL_GPIO_IsInputPinSet(AD7142_SDA_Port2, AD7142_SDA_Pin2))
	{
		ErrTime++;
		if(ErrTime>250)
		{
			IIC_Stop2();
			return 0;
		}
	}
	LL_GPIO_ResetOutputPin(AD7142_SCL_Port2, AD7142_SCL_Pin2);//时钟输出0 	   
	return 1;  
} 

void IIC_Ack2(void){             //发送ACKM应答
	uint8_t a=0;
	LL_GPIO_SetPinMode(AD7142_SDA_Port2, AD7142_SDA_Pin2, LL_GPIO_MODE_OUTPUT);
	LL_GPIO_ResetOutputPin(AD7142_SDA_Port2, AD7142_SDA_Pin2);
	a = delay_cycle;while(a--){__NOP();}
	LL_GPIO_SetOutputPin(AD7142_SCL_Port2, AD7142_SCL_Pin2);
	a = delay_cycle;while(a--){__NOP();}
	LL_GPIO_ResetOutputPin(AD7142_SCL_Port2, AD7142_SCL_Pin2);
}
	    
void IIC_NAck2(void){            //发送NACKM应答	
	uint8_t a;
	LL_GPIO_SetPinMode(AD7142_SDA_Port2, AD7142_SDA_Pin2, LL_GPIO_MODE_OUTPUT);
	LL_GPIO_SetOutputPin(AD7142_SDA_Port2, AD7142_SDA_Pin2);
	a = delay_cycle;while(a--){__NOP();}
	LL_GPIO_SetOutputPin(AD7142_SCL_Port2, AD7142_SCL_Pin2);
	a = delay_cycle;while(a--){__NOP();}
	LL_GPIO_ResetOutputPin(AD7142_SCL_Port2, AD7142_SCL_Pin2);
}					 				     
 
void IIC_Send_Byte2(uint8_t txd){     //发送1个字节（只包含8个SCL时钟周期的时序）
	uint8_t a;
	LL_GPIO_SetPinMode(AD7142_SDA_Port2, AD7142_SDA_Pin2, LL_GPIO_MODE_OUTPUT);
	for(uint8_t t=0; t<8; t++){
		if((txd&0x80)!=0)
			LL_GPIO_SetOutputPin(AD7142_SDA_Port2, AD7142_SDA_Pin2);
		else
			LL_GPIO_ResetOutputPin(AD7142_SDA_Port2, AD7142_SDA_Pin2);
	  txd<<=1; 
		a = delay_cycle;while(a--){__NOP();}
		LL_GPIO_SetOutputPin(AD7142_SCL_Port2, AD7142_SCL_Pin2);
		a = delay_cycle;while(a--){__NOP();}
		LL_GPIO_ResetOutputPin(AD7142_SCL_Port2, AD7142_SCL_Pin2);
	}	
	IIC_Wait_Ack2();
}

uint8_t IIC_Receive_Byte2(void){      //接收1个字节（只包含8个SCL时钟周期的时序）
	uint8_t receive=0,a=0;
	LL_GPIO_SetPinMode(AD7142_SDA_Port2, AD7142_SDA_Pin2, LL_GPIO_MODE_INPUT);//SDA设置为输入
  for(uint8_t i=0; i<7; i++){
		LL_GPIO_SetOutputPin(AD7142_SCL_Port2, AD7142_SCL_Pin2);
		//a = delay_cycle>>1;while(a--){__NOP();}
		receive += (LL_GPIO_IsInputPinSet(AD7142_SDA_Port2, AD7142_SDA_Pin2))&0x01;
		LL_GPIO_ResetOutputPin(AD7142_SCL_Port2, AD7142_SCL_Pin2); 
		receive<<=1;
		//a = delay_cycle>>1;while(a--){__NOP();}
		a = delay_cycle;while(a--){__NOP();}
	}
	LL_GPIO_SetOutputPin(AD7142_SCL_Port2, AD7142_SCL_Pin2);
	a = delay_cycle>>1;while(a--){__NOP();}
	receive += (LL_GPIO_IsInputPinSet(AD7142_SDA_Port2, AD7142_SDA_Pin2))&0x01;
	LL_GPIO_ResetOutputPin(AD7142_SCL_Port2, AD7142_SCL_Pin2); 
	a = delay_cycle>>1;while(a--){__NOP();}
	a = delay_cycle;while(a--){__NOP();}
	return receive;
}

void AD7142_Write2(uint16_t addr,uint16_t data){
	IIC_Start2();
	IIC_Send_Byte2(AD7142_IIC_Addr1 | 0x00);
	IIC_Send_Byte2(addr>>8);
	IIC_Send_Byte2(addr&0xff);
	IIC_Send_Byte2(data>>8);
	IIC_Send_Byte2(data&0xff);
	IIC_Stop2();	
}

uint16_t AD7142_Read2(uint16_t addr,uint16_t* result,uint16_t size){
	if(size){
		IIC_Start2();
		IIC_Send_Byte2(AD7142_IIC_Addr1 | 0x00);
		IIC_Send_Byte2(addr>>8);
		IIC_Send_Byte2(addr&0xff);
		IIC_Start2();
		IIC_Send_Byte2(AD7142_IIC_Addr1 | 0x01);
		while(--size){
			*result = IIC_Receive_Byte2()<<8;
			IIC_Ack2();
			*result |= IIC_Receive_Byte2();
			IIC_Ack2();
			result++;
		}
		*result = IIC_Receive_Byte2()<<8;
		IIC_Ack2();
		*result |= IIC_Receive_Byte2();
		IIC_NAck2();
	}
	else{
		IIC_Start2();
		IIC_Send_Byte2(AD7142_IIC_Addr2 | 0x00);
		IIC_Send_Byte2(addr>>8);
		IIC_Send_Byte2(addr&0xff);
		IIC_Start2();
		IIC_Send_Byte2(AD7142_IIC_Addr2 | 0x01);
		IIC_Receive_Byte2();
		IIC_Ack2();
		IIC_Receive_Byte2();
		IIC_NAck2();
	}
	IIC_Stop2();
	return 1;
}


	#else
uint16_t AD7142_RWbyte(uint16_t comd){
	while (SPI_I2S_GetFlagStatus(SPI1, SPI_I2S_FLAG_TXE) == RESET){}//等待发送区空
		SPI_I2S_SendData(SPI1, comd);
	while (SPI_I2S_GetFlagStatus(SPI1, SPI_I2S_FLAG_BSY) != RESET){}//等待发送结束
  while (SPI_I2S_GetFlagStatus(SPI1, SPI_I2S_FLAG_RXNE) == RESET){} //等待接收完一个byte 
	return SPI_I2S_ReceiveData(SPI1);
}

void AD7142_Write1(uint16_t addr,uint16_t comd){
	AD7142_CS1_ENABLE;
	AD7142_RWbyte(AD7142_WRITE | addr);
	AD7142_RWbyte(comd);
	AD7142_CS1_DISABLE;
}

uint16_t AD7142_Read1(uint16_t addr,uint16_t* result,uint16_t size){
	if(size){
		AD7142_CS1_ENABLE;
		AD7142_RWbyte(AD7142_WRITE | addr);
		while(size--){
			*result = AD7142_RWbyte(0xFFFF);
			result++;
		}
		AD7142_CS1_DISABLE;
		return 1;
	}
	else
		return 0;
}

void AD7142_Write2(uint16_t addr,uint16_t comd){
	AD7142_CS2_ENABLE;
	AD7142_RWbyte(AD7142_WRITE | addr);
	AD7142_RWbyte(comd);
	AD7142_CS2_DISABLE;
}

uint16_t AD7142_Read2(uint16_t addr,uint16_t* result,uint16_t size){
	if(size){
		AD7142_CS2_ENABLE;
		AD7142_RWbyte(AD7142_WRITE | addr);
		while(size--){
			*result = AD7142_RWbyte(0xFFFF);
			result++;
		}
		AD7142_CS2_DISABLE;
		return 1;
	}
	else
		return 0;
}

	void AD7142_Protocol_init(void){

	}

	#endif

void AD7142_init(void){
	AD7142_Write1(PWR_CONTROL,0x0400);		//	复位第一片7142的所有寄存器
	
	HAL_Delay(1);
	
	//AD7142寄存器设置 第一片（接在INT1的）
	//第一步设置：配置寄存器二
	
	//STAGE0设置,只有CIN0接入CDC+
	AD7142_Write1(STAGE0_SETTING_REG+0,0x3FFE);
	AD7142_Write1(STAGE0_SETTING_REG+1,0x7FFF);
	AD7142_Write1(STAGE0_SETTING_REG+2,0x0000);//调CIN偏置
	AD7142_Write1(STAGE0_SETTING_REG+3,0x5F5F);//调CHIN灵敏度
	AD7142_Write1(STAGE0_SETTING_REG+4,0x6665);
	AD7142_Write1(STAGE0_SETTING_REG+5,0x6665);
	AD7142_Write1(STAGE0_SETTING_REG+6,0x7FFF);
	AD7142_Write1(STAGE0_SETTING_REG+7,0x7FFF);
	//STAGE1设置,只有CIN1接入CDC+
	AD7142_Write1(STAGE1_SETTING_REG+0,0x3FFB);
	AD7142_Write1(STAGE1_SETTING_REG+1,0x7FFF);
	AD7142_Write1(STAGE1_SETTING_REG+2,0x0000);//调CIN偏置
	AD7142_Write1(STAGE1_SETTING_REG+3,0x5F5F);//调CHIN灵敏度
	AD7142_Write1(STAGE1_SETTING_REG+4,0x6665);
	AD7142_Write1(STAGE1_SETTING_REG+5,0x6665);
	AD7142_Write1(STAGE1_SETTING_REG+6,0x7FFF);
	AD7142_Write1(STAGE1_SETTING_REG+7,0x7FFF);
	//STAGE2设置,只有CIN2接入CDC+
	AD7142_Write1(STAGE2_SETTING_REG+0,0x3FEF);
	AD7142_Write1(STAGE2_SETTING_REG+1,0x7FFF);
	AD7142_Write1(STAGE2_SETTING_REG+2,0x0800);//调CIN偏置
	AD7142_Write1(STAGE2_SETTING_REG+3,0x5F5F);//调CHIN灵敏度
	AD7142_Write1(STAGE2_SETTING_REG+4,0x6665);
	AD7142_Write1(STAGE2_SETTING_REG+5,0x6665);
	AD7142_Write1(STAGE2_SETTING_REG+6,0x7FFF);
	AD7142_Write1(STAGE2_SETTING_REG+7,0x7FFF);
	//STAGE3设置,只有CIN3接入CDC+
	AD7142_Write1(STAGE3_SETTING_REG+0,0x3FBF);
	AD7142_Write1(STAGE3_SETTING_REG+1,0x7FFF);
	AD7142_Write1(STAGE3_SETTING_REG+2,0x0400);//调CIN偏置
	AD7142_Write1(STAGE3_SETTING_REG+3,0x5F5F);//调CHIN灵敏度
	AD7142_Write1(STAGE3_SETTING_REG+4,0x6665);
	AD7142_Write1(STAGE3_SETTING_REG+5,0x6665);
	AD7142_Write1(STAGE3_SETTING_REG+6,0x7FFF);
	AD7142_Write1(STAGE3_SETTING_REG+7,0x7FFF);
	//STAGE4设置,只有CIN4接入CDC+
	AD7142_Write1(STAGE4_SETTING_REG+0,0x3EFF);
	AD7142_Write1(STAGE4_SETTING_REG+1,0x7FFF);
	AD7142_Write1(STAGE4_SETTING_REG+2,0x0500);//调CIN偏置
	AD7142_Write1(STAGE4_SETTING_REG+3,0x5F5F);//调CHIN灵敏度
	AD7142_Write1(STAGE4_SETTING_REG+4,0x6665);
	AD7142_Write1(STAGE4_SETTING_REG+5,0x6665);
	AD7142_Write1(STAGE4_SETTING_REG+6,0x7FFF);
	AD7142_Write1(STAGE4_SETTING_REG+7,0x7FFF);
	//STAGE5设置,只有CIN12接入CDC+
	AD7142_Write1(STAGE5_SETTING_REG+0,0x3FFF);
	AD7142_Write1(STAGE5_SETTING_REG+1,0x7BFF);
	AD7142_Write1(STAGE5_SETTING_REG+2,0x0800);//调CIN偏置
	AD7142_Write1(STAGE5_SETTING_REG+3,0x5F5F);//调CHIN灵敏度
	AD7142_Write1(STAGE5_SETTING_REG+4,0x6665);
	AD7142_Write1(STAGE5_SETTING_REG+5,0x6665);
	AD7142_Write1(STAGE5_SETTING_REG+6,0x7FFF);
	AD7142_Write1(STAGE5_SETTING_REG+7,0x7FFF);
	//STAGE6设置,只有CIN13接入CDC+
	AD7142_Write1(STAGE6_SETTING_REG+0,0x3FFF);
	AD7142_Write1(STAGE6_SETTING_REG+1,0x6FFF);
	AD7142_Write1(STAGE6_SETTING_REG+2,0x0800);//调CIN偏置
	AD7142_Write1(STAGE6_SETTING_REG+3,0x5F5F);//调CHIN灵敏度
	AD7142_Write1(STAGE6_SETTING_REG+4,0x6665);
	AD7142_Write1(STAGE6_SETTING_REG+5,0x6665);
	AD7142_Write1(STAGE6_SETTING_REG+6,0x7FFF);
	AD7142_Write1(STAGE6_SETTING_REG+7,0x7FFF);
//	//STAGE7设置,只有CIN7接入CDC+
//	AD7142_Write1(STAGE7_SETTING_REG+0,0x3FFF);
//	AD7142_Write1(STAGE7_SETTING_REG+1,0x7FFE);
//	AD7142_Write1(STAGE7_SETTING_REG+2,0x0900);//调CIN偏置
//	AD7142_Write1(STAGE7_SETTING_REG+3,0x5F5F);//调CHIN灵敏度
//	AD7142_Write1(STAGE7_SETTING_REG+4,0x6665);
//	AD7142_Write1(STAGE7_SETTING_REG+5,0x6665);
//	AD7142_Write1(STAGE7_SETTING_REG+6,0x7FFF);
//	AD7142_Write1(STAGE7_SETTING_REG+7,0x7FFF);
//	//STAGE8设置,只有CIN8接入CDC+
//	AD7142_Write1(STAGE8_SETTING_REG+0,0x3FFF);
//	AD7142_Write1(STAGE8_SETTING_REG+1,0x7FFB);
//	AD7142_Write1(STAGE8_SETTING_REG+2,0x0800);//调CIN偏置
//	AD7142_Write1(STAGE8_SETTING_REG+3,0x5F5F);//调CHIN灵敏度
//	AD7142_Write1(STAGE8_SETTING_REG+4,0x6665);
//	AD7142_Write1(STAGE8_SETTING_REG+5,0x6665);
//	AD7142_Write1(STAGE8_SETTING_REG+6,0x7FFF);
//	AD7142_Write1(STAGE8_SETTING_REG+7,0x7FFF);
	//STAGE7设置,只有CIN9接入CDC+
	AD7142_Write1(STAGE7_SETTING_REG+0,0x3FFF);
	AD7142_Write1(STAGE7_SETTING_REG+1,0x7FEF);
	AD7142_Write1(STAGE7_SETTING_REG+2,0x0700);//调CIN偏置
	AD7142_Write1(STAGE7_SETTING_REG+3,0x5F5F);//调CHIN灵敏度
	AD7142_Write1(STAGE7_SETTING_REG+4,0x6665);
	AD7142_Write1(STAGE7_SETTING_REG+5,0x6665);
	AD7142_Write1(STAGE7_SETTING_REG+6,0x7FFF);
	AD7142_Write1(STAGE7_SETTING_REG+7,0x7FFF);
	//STAGE8设置,只有CIN10接入CDC+
	AD7142_Write1(STAGE8_SETTING_REG+0,0x3FFF);
	AD7142_Write1(STAGE8_SETTING_REG+1,0x7FBF);
	AD7142_Write1(STAGE8_SETTING_REG+2,0x0500);//调CIN偏置
	AD7142_Write1(STAGE8_SETTING_REG+3,0x5F5F);//调CHIN灵敏度
	AD7142_Write1(STAGE8_SETTING_REG+4,0x6665);
	AD7142_Write1(STAGE8_SETTING_REG+5,0x6665);
	AD7142_Write1(STAGE8_SETTING_REG+6,0x7FFF);
	AD7142_Write1(STAGE8_SETTING_REG+7,0x7FFF);
	//STAGE9设置,只有CIN11接入CDC+
	AD7142_Write1(STAGE9_SETTING_REG+0,0x3FFF);
	AD7142_Write1(STAGE9_SETTING_REG+1,0x7EFF);
	AD7142_Write1(STAGE9_SETTING_REG+2,0x00B0);//调CIN偏置
	AD7142_Write1(STAGE9_SETTING_REG+3,0x5F5F);//调CHIN灵敏度
	AD7142_Write1(STAGE9_SETTING_REG+4,0x6665);
	AD7142_Write1(STAGE9_SETTING_REG+5,0x6665);
	AD7142_Write1(STAGE9_SETTING_REG+6,0x7FFF);
	AD7142_Write1(STAGE9_SETTING_REG+7,0x7FFF);

	
	
	HAL_Delay(1);
	
	
	
	//***************************************************************//
	AD7142_Write2(PWR_CONTROL,0x0400);		//	复位第二片7142的所有寄存器
	
	HAL_Delay(1);
	
	//AD7142寄存器设置 第二片（接在INT2的）
	//第一步设置：配置寄存器二
		//STAGE0设置,只有CIN0接入CDC+
	AD7142_Write2(STAGE0_SETTING_REG+0,0x3FFE);
	AD7142_Write2(STAGE0_SETTING_REG+1,0x7FFF);
	AD7142_Write2(STAGE0_SETTING_REG+2,0x0000);//调CIN偏置
	AD7142_Write2(STAGE0_SETTING_REG+3,0x5F5F);//调CHIN灵敏度
	AD7142_Write2(STAGE0_SETTING_REG+4,0x6665);
	AD7142_Write2(STAGE0_SETTING_REG+5,0x6665);
	AD7142_Write2(STAGE0_SETTING_REG+6,0x7FFF);
	AD7142_Write2(STAGE0_SETTING_REG+7,0x7FFF);
	//STAGE1设置,只有CIN1接入CDC+
	AD7142_Write2(STAGE1_SETTING_REG+0,0x3FFB);
	AD7142_Write2(STAGE1_SETTING_REG+1,0x7FFF);
	AD7142_Write2(STAGE1_SETTING_REG+2,0x0000);//调CIN偏置
	AD7142_Write2(STAGE1_SETTING_REG+3,0x5F5F);//调CHIN灵敏度
	AD7142_Write2(STAGE1_SETTING_REG+4,0x6665);
	AD7142_Write2(STAGE1_SETTING_REG+5,0x6665);
	AD7142_Write2(STAGE1_SETTING_REG+6,0x7FFF);
	AD7142_Write2(STAGE1_SETTING_REG+7,0x7FFF);
	//STAGE2设置,只有CIN2接入CDC+
	AD7142_Write2(STAGE2_SETTING_REG+0,0x3FEF);
	AD7142_Write2(STAGE2_SETTING_REG+1,0x7FFF);
	AD7142_Write2(STAGE2_SETTING_REG+2,0x0800);//调CIN偏置
	AD7142_Write2(STAGE2_SETTING_REG+3,0x5F5F);//调CHIN灵敏度
	AD7142_Write2(STAGE2_SETTING_REG+4,0x6665);
	AD7142_Write2(STAGE2_SETTING_REG+5,0x6665);
	AD7142_Write2(STAGE2_SETTING_REG+6,0x7FFF);
	AD7142_Write2(STAGE2_SETTING_REG+7,0x7FFF);
	//STAGE3设置,只有CIN3接入CDC+
	AD7142_Write2(STAGE3_SETTING_REG+0,0x3FBF);
	AD7142_Write2(STAGE3_SETTING_REG+1,0x7FFF);
	AD7142_Write2(STAGE3_SETTING_REG+2,0x0400);//调CIN偏置
	AD7142_Write2(STAGE3_SETTING_REG+3,0x5F5F);//调CHIN灵敏度
	AD7142_Write2(STAGE3_SETTING_REG+4,0x6665);
	AD7142_Write2(STAGE3_SETTING_REG+5,0x6665);
	AD7142_Write2(STAGE3_SETTING_REG+6,0x7FFF);
	AD7142_Write2(STAGE3_SETTING_REG+7,0x7FFF);
	//STAGE4设置,只有CIN4接入CDC+
	AD7142_Write2(STAGE4_SETTING_REG+0,0x3EFF);
	AD7142_Write2(STAGE4_SETTING_REG+1,0x7FFF);
	AD7142_Write2(STAGE4_SETTING_REG+2,0x0500);//调CIN偏置
	AD7142_Write2(STAGE4_SETTING_REG+3,0x5F5F);//调CHIN灵敏度
	AD7142_Write2(STAGE4_SETTING_REG+4,0x6665);
	AD7142_Write2(STAGE4_SETTING_REG+5,0x6665);
	AD7142_Write2(STAGE4_SETTING_REG+6,0x7FFF);
	AD7142_Write2(STAGE4_SETTING_REG+7,0x7FFF);
	//STAGE5设置,只有CIN12接入CDC+
	AD7142_Write2(STAGE5_SETTING_REG+0,0x3FFF);
	AD7142_Write2(STAGE5_SETTING_REG+1,0x7BFF);
	AD7142_Write2(STAGE5_SETTING_REG+2,0x0800);//调CIN偏置
	AD7142_Write2(STAGE5_SETTING_REG+3,0x5F5F);//调CHIN灵敏度
	AD7142_Write2(STAGE5_SETTING_REG+4,0x6665);
	AD7142_Write2(STAGE5_SETTING_REG+5,0x6665);
	AD7142_Write2(STAGE5_SETTING_REG+6,0x7FFF);
	AD7142_Write2(STAGE5_SETTING_REG+7,0x7FFF);
	//STAGE6设置,只有CIN13接入CDC+
	AD7142_Write2(STAGE6_SETTING_REG+0,0x3FFF);
	AD7142_Write2(STAGE6_SETTING_REG+1,0x6FFF);
	AD7142_Write2(STAGE6_SETTING_REG+2,0x0800);//调CIN偏置
	AD7142_Write2(STAGE6_SETTING_REG+3,0x5F5F);//调CHIN灵敏度
	AD7142_Write2(STAGE6_SETTING_REG+4,0x6665);
	AD7142_Write2(STAGE6_SETTING_REG+5,0x6665);
	AD7142_Write2(STAGE6_SETTING_REG+6,0x7FFF);
	AD7142_Write2(STAGE6_SETTING_REG+7,0x7FFF);
//	//STAGE7设置,只有CIN7接入CDC+
//	AD7142_Write2(STAGE7_SETTING_REG+0,0x3FFF);
//	AD7142_Write2(STAGE7_SETTING_REG+1,0x7FFE);
//	AD7142_Write2(STAGE7_SETTING_REG+2,0x0900);//调CIN偏置
//	AD7142_Write2(STAGE7_SETTING_REG+3,0x5F5F);//调CHIN灵敏度
//	AD7142_Write2(STAGE7_SETTING_REG+4,0x6665);
//	AD7142_Write2(STAGE7_SETTING_REG+5,0x6665);
//	AD7142_Write2(STAGE7_SETTING_REG+6,0x7FFF);
//	AD7142_Write2(STAGE7_SETTING_REG+7,0x7FFF);
//	//STAGE8设置,只有CIN8接入CDC+
//	AD7142_Write2(STAGE8_SETTING_REG+0,0x3FFF);
//	AD7142_Write2(STAGE8_SETTING_REG+1,0x7FFB);
//	AD7142_Write2(STAGE8_SETTING_REG+2,0x0800);//调CIN偏置
//	AD7142_Write2(STAGE8_SETTING_REG+3,0x5F5F);//调CHIN灵敏度
//	AD7142_Write2(STAGE8_SETTING_REG+4,0x6665);
//	AD7142_Write2(STAGE8_SETTING_REG+5,0x6665);
//	AD7142_Write2(STAGE8_SETTING_REG+6,0x7FFF);
//	AD7142_Write2(STAGE8_SETTING_REG+7,0x7FFF);
	//STAGE7设置,只有CIN9接入CDC+
	AD7142_Write2(STAGE7_SETTING_REG+0,0x3FFF);
	AD7142_Write2(STAGE7_SETTING_REG+1,0x7FEF);
	AD7142_Write2(STAGE7_SETTING_REG+2,0x0700);//调CIN偏置
	AD7142_Write2(STAGE7_SETTING_REG+3,0x5F5F);//调CHIN灵敏度
	AD7142_Write2(STAGE7_SETTING_REG+4,0x6665);
	AD7142_Write2(STAGE7_SETTING_REG+5,0x6665);
	AD7142_Write2(STAGE7_SETTING_REG+6,0x7FFF);
	AD7142_Write2(STAGE7_SETTING_REG+7,0x7FFF);
	//STAGE8设置,只有CIN10接入CDC+
	AD7142_Write2(STAGE8_SETTING_REG+0,0x3FFF);
	AD7142_Write2(STAGE8_SETTING_REG+1,0x7FBF);
	AD7142_Write2(STAGE8_SETTING_REG+2,0x0500);//调CIN偏置
	AD7142_Write2(STAGE8_SETTING_REG+3,0x5F5F);//调CHIN灵敏度
	AD7142_Write2(STAGE8_SETTING_REG+4,0x6665);
	AD7142_Write2(STAGE8_SETTING_REG+5,0x6665);
	AD7142_Write2(STAGE8_SETTING_REG+6,0x7FFF);
	AD7142_Write2(STAGE8_SETTING_REG+7,0x7FFF);
	//STAGE9设置,只有CIN11接入CDC+
	AD7142_Write2(STAGE9_SETTING_REG+0,0x3FFF);
	AD7142_Write2(STAGE9_SETTING_REG+1,0x7EFF);
	AD7142_Write2(STAGE9_SETTING_REG+2,0x00B0);//调CIN偏置
	AD7142_Write2(STAGE9_SETTING_REG+3,0x5F5F);//调CHIN灵敏度
	AD7142_Write2(STAGE9_SETTING_REG+4,0x6665);
	AD7142_Write2(STAGE9_SETTING_REG+5,0x6665);
	AD7142_Write2(STAGE9_SETTING_REG+6,0x7FFF);
	AD7142_Write2(STAGE9_SETTING_REG+7,0x7FFF);


	
	HAL_Delay(1);
	
	//第二步设置：配置寄存器1
	AD7142_Write1(PWR_CONTROL,0x2190);// 全功耗模式，decimation=128，序列长度=10个stage(Stage0~Stage9)
	AD7142_Write1(STAGE_CAL_EN,0x0000);// 禁用所有stage的内部校准，改由MCU软件调零
	AD7142_Write1(AMB_COMP_CTRL0,0x3FF0);//0x0002
	AD7142_Write1(AMB_COMP_CTRL1,0x14C8);//0x0003
	AD7142_Write1(AMB_COMP_CTRL2,0x0832);//0x0004
	AD7142_Write1(STAGE_COMPLETE_INT_EN,0x0000);// 先关闭所有stage的转换完成中断
	
	HAL_Delay(1);

	//第三步设置
//	AD7142_Write1(STAGE_CAL_EN,0x0FFF);// 若采用芯片内部校准，可在完成初始化后再打开对应stage
	// 清除CDC转换完成中断状态位
	AD7142_Read1(STAGE_COMPLETE_INT_STATUS,NULL,0);
	
	//第二步设置：配置寄存器1
	AD7142_Write2(PWR_CONTROL,0x2190);// 全功耗模式，decimation=128，序列长度=10个stage(Stage0~Stage9)
	AD7142_Write2(STAGE_CAL_EN,0x0000);// 禁用所有stage的内部校准，改由MCU软件调零
	AD7142_Write2(AMB_COMP_CTRL0,0x3FF0);//0x0002
	AD7142_Write2(AMB_COMP_CTRL1,0x14C8);//0x0003
	AD7142_Write2(AMB_COMP_CTRL2,0x0832);//0x0004
	AD7142_Write2(STAGE_COMPLETE_INT_EN,0x0000);// 先关闭所有stage的转换完成中断
	
	HAL_Delay(1);

	//第三步设置
//	AD7142_Write2(STAGE_CAL_EN,0x0FFF);// 若采用芯片内部校准，可在完成初始化后再打开对应stage
	// 清除CDC转换完成中断状态位
	AD7142_Read2(STAGE_COMPLETE_INT_STATUS,NULL,0);
}
