#ifndef __COMPEN_H
#define __COMPEN_H 			   
#include "main.h"	  
#include <stdint.h>

// 定义滞后补偿系数结构体
typedef struct {
    float quadraticMultiplier;
    float linearMultiplier;
    float offset;
} HysteresisCoefficients;

// 定义滞后补偿系数集
typedef struct {
    HysteresisCoefficients coefficients[3];
} HysteresisSet;

// 定义结构体来存补偿系数
typedef struct {
    float num1;
    float num2;
    float num3;
    float num4;
    float num5;
    float num6;
	    float num7;
	    float num8;
	    float num9;
	    float num10;
	    float num11;
	    float num12;
	    float num13;
	    float num14;
	    float num15;
	    float num16;
} CompenNum;

// 定义一个结构体来存储一组标定系数
typedef struct {
    CompenNum coefficients[3];
} ComPenSet;

//G 温度补偿系数
typedef struct {
    float p00;
    float p10;
    float p01;
    float p20;
    float p11;
    float p02;
} Coefficients;

void CompenFunction(int16_t *rawCap, int16_t *comCap, int setIndex,int zeroFlag);
int16_t TemperatureCompen(int16_t standardCap, int16_t* capIn, int sensorIndex, int16_t* resultTemp);
int16_t calculateHysteresisCompensation(int16_t value, HysteresisCoefficients coeffs);
void HysteresisCompen(int16_t *inCap, int16_t* resultHysteresis,int setIndex);
void filter(int16_t * Data, size_t size, float AFA);


#endif // _COMPEN_H_