#include "AutoOffset.h"
#include "AD7142.h"
#include "math.h"

// 原始输出与偏移量
uint16_t No_Offset_Data[15] = {0};
int16_t RawData_Offset[15] = {0};
uint16_t No_Offset_Data2[15] = {0};
int16_t RawData_Offset2[15] = {0};

int16_t offset_init[2][10] = {-6,-5,8,-3,-10,-11,-6,-5,-3,-5,       -6,-5,8,-3,-10,-11,-6,-5,-3,-5};

// 寄存器写入标志位与写入数据，�?表示调整偏移完成，无需再次调整
uint8_t Reg_Offset_Done_flag[15] = {0};
uint16_t flag_sum = 0;
uint8_t Reg_Offset_Done_flag2[15] = {0};
uint16_t flag_sum2 = 0;

// 自动校准完成标志
_Bool Auto_Offset_Done = 0;
_Bool Auto_Offset_Done2 = 0;

//偏差标志位，0为正�?为负
uint8_t sign_offset_reg = 0;
uint8_t sign_offset_reg2 = 0;

void Auto_Offset(void)
{
  if (Auto_Offset_Done == 0)
  {
    // 禁用中断
    AD7142_Write1(STAGE_COMPLETE_INT_EN, 0x0000);
    for (uint8_t j = 0; j <= Stage_Number; j++)
    {
      AD7142_Read1(CDC_RESULT_S0 + j, No_Offset_Data + j, 1);
    }
    int32_t offset_raw;//int32防止65535-30000导致溢出
    uint16_t abs_offset_raw;
    uint16_t write_data;
    uint16_t write_addr;
	  uint16_t sign_addr;
	  uint16_t sign_data;

    // 计算偏移�?
    for (uint8_t i = 0; i <= Stage_Number; i++)
    {
      offset_raw = No_Offset_Data[i] - offset_base;
      abs_offset_raw = abs(offset_raw);
      write_addr = STAGE0_SETTING_REG + 2 + 8 * i;
	   sign_addr = STAGE0_SETTING_REG + 1 + 8 * i;
      write_data = RawData_Offset[i];

	  /* 调整正负偏置 */
	  if(sign_offset_reg == 0)
	  {
		  AD7142_Read1(sign_addr, &sign_data, 1);//加正偏置
		  sign_data &= ~(1 << 15);
		  sign_data |= (1 << 14);
      write_data << 8;
      write_data |= (1 << 15);

	  }
	  else
	  {
		  AD7142_Read1(sign_addr, &sign_data, 1);//加负偏置
		  sign_data |= (1 << 15);  // 将第15位置1
		  sign_data &= ~(1 << 14); // 将第14位置0
      write_data |= (1 << 7);
	  }

      if(RawData_Offset[i] <= 127 )
      {
        RawData_Offset[i]++;
      }
      else if(sign_offset_reg == 0)
      {
        for(int k = 0;k <= Stage_Number;k++)
		  {
			  RawData_Offset[k] = 0;
		  }
        sign_offset_reg = 1;
      }
	  
      if (abs_offset_raw > offset_threshold)
      {
        AD7142_Write1(sign_addr, sign_data);
		    AD7142_Write1(write_addr, write_data);
        Reg_Offset_Done_flag[i] = 0;
        //LL_mDelay(1);
				for (volatile uint32_t i = 0; i < 1000; i++) {
// 空循环，创建延迟
}
      }
	  else
	  {
	    Reg_Offset_Done_flag[i] = 1;
	  }
    }
	
	flag_sum = 0;
	for(int cnt = 0;cnt <= Stage_Number;cnt++)
	{
	  flag_sum += Reg_Offset_Done_flag[cnt];
	}
	if(flag_sum == Stage_Number + 1)
	{
		Auto_Offset_Done = 1;
	}
	
    //LL_mDelay(10);
	for (volatile uint32_t i = 0; i < 65535; i++) {
// 空循环，创建延迟
}
    // ���´�Stage9ת������жϣ�������ж�״̬λ
    AD7142_Write1(STAGE_COMPLETE_INT_EN, 0x0200);
    AD7142_Read1(STAGE_COMPLETE_INT_STATUS, NULL, 1);
  }
}

void Auto_Offset2(void)
{
  if (Auto_Offset_Done2 == 0)
  {
    // 禁用中断
    AD7142_Write2(STAGE_COMPLETE_INT_EN, 0x0000);
    for (uint8_t j = 0; j <= Stage_Number; j++)
    {
      AD7142_Read2(CDC_RESULT_S0 + j, No_Offset_Data2 + j, 1);
    }
    int32_t offset_raw;//int32防止65535-30000导致溢出
    uint16_t abs_offset_raw;
    uint16_t write_data;
    uint16_t write_addr;
	  uint16_t sign_addr;
	  uint16_t sign_data;

    // 计算偏移�?
    for (uint8_t i = 0; i <= Stage_Number; i++)
    {
      offset_raw = No_Offset_Data2[i] - offset_base;
      abs_offset_raw = abs(offset_raw);
      write_addr = STAGE0_SETTING_REG + 2 + 8 * i;
	   sign_addr = STAGE0_SETTING_REG + 1 + 8 * i;
      write_data = RawData_Offset2[i];

	  /* 调整正负偏置 */
	  if(sign_offset_reg2 == 0)
	  {
		  AD7142_Read2(sign_addr, &sign_data, 1);//加正偏置
		  sign_data &= ~(1 << 15);
		  sign_data |= (1 << 14);
      write_data << 8;
      write_data |= (1 << 15);

	  }
	  else
	  {
		  AD7142_Read2(sign_addr, &sign_data, 1);//加负偏置
		  sign_data |= (1 << 15);  // 将第15位置1
		  sign_data &= ~(1 << 14); // 将第14位置0
      write_data |= (1 << 7);
	  }

      if(RawData_Offset2[i] <= 127 )
      {
        RawData_Offset2[i]++;
      }
      else if(sign_offset_reg2 == 0)
      {
        for(int k = 0;k <= Stage_Number;k++)
		  {
			  RawData_Offset2[k] = 0;
		  }
        sign_offset_reg2 = 1;
      }
	  
      if (abs_offset_raw > offset_threshold)
      {
        AD7142_Write2(sign_addr, sign_data);
		    AD7142_Write2(write_addr, write_data);
        Reg_Offset_Done_flag2[i] = 0;
        //LL_mDelay(1);
				for (volatile uint32_t i = 0; i < 1000; i++) {
// 空循环，创建延迟
}
      }
	  else
	  {
	    Reg_Offset_Done_flag2[i] = 1;
	  }
    }
	
	flag_sum2 = 0;
	for(int cnt = 0;cnt <= Stage_Number;cnt++)
	{
	  flag_sum2 += Reg_Offset_Done_flag2[cnt];
	}
	if(flag_sum2 == Stage_Number + 1)
	{
		Auto_Offset_Done2 = 1;
	}
	
    //LL_mDelay(10);
	for (volatile uint32_t i = 0; i < 65535; i++) {
// 空循环，创建延迟
}

    // ���´�Stage9ת������жϣ�������ж�״̬λ
    AD7142_Write2(STAGE_COMPLETE_INT_EN, 0x0200);
    AD7142_Read2(STAGE_COMPLETE_INT_STATUS, NULL, 1);
  }
}
void Write_offset(int sensor_number)
{
	uint16_t write_data;
    uint16_t write_addr;
    uint16_t sign_addr;
    uint16_t sign_data;
    for (uint8_t i = 0; i <= 6; i++)
    {
      write_addr = STAGE0_SETTING_REG + 2 + 8 * i;
	  sign_addr = STAGE0_SETTING_REG + 1 + 8 * i;
      write_data = abs(offset_init[sensor_number][i]);

	  /* 调整正负偏置 */
	  if(offset_init[sensor_number][i] >= 0)
	  {
		  AD7142_Read1(sign_addr, &sign_data, 1);//加正偏置
		  sign_data &= ~(1 << 15);
		  sign_data |= (1 << 14);
      write_data << 8;
      write_data |= (1 << 15);
	  }
	  else
	  {
		  AD7142_Read1(sign_addr, &sign_data, 1);//加负偏置
		  sign_data |= (1 << 15);  // 将第15位置1
		  sign_data &= ~(1 << 14); // 将第14位置0
      write_data |= (1 << 7);
	  }
        AD7142_Write1(sign_addr, sign_data);
		AD7142_Write1(write_addr, write_data);
    }
}
