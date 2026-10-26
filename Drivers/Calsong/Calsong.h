#ifndef  __CALSONG_H
#define __CALSONG_H


#include "main.h"

void cal_L(int16_t* inCap,float* outL);
void cal_QNM(float* inL,float* outM,float* outN,float* outQ);
void cal_d(float* inM,float* inN,float* inQ,float* outd,float* outj1,float* outj2);
#endif // ! __AutoOffset_H
