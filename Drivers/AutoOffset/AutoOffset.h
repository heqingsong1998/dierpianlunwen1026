#ifndef  __AutoOffset_H
#define __AutoOffset_H

#include "AD7142.h"

#define Stage_Number 9 // 仅Stage0~Stage9有效，对应10路已连接的CIN

// 鍋忕Щ閲忓熀鍑嗕笌闃堝�?
#define offset_base  30000
#define offset_threshold  2000

void Auto_Offset(void);
void Auto_Offset2(void);
void Write_offset(int sensor_number);

#endif // ! __AutoOffset_H
