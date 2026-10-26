#include "Calsong.h"
#include "math.h"
#define CONST_A (122.07747 + 11780.70194)
#define CONST_B (24 * 18)
#define deg (180.0/3.14)

void cal_L(int16_t* inCap,float* outL)
{
	
	float temp1 = pow(fabs((CONST_A/(inCap[0]-122.07747))-1),1/1.16926)*0.30468;
	float temp2 = pow(fabs((CONST_A/(inCap[1]-122.07747))-1),1/1.16926)*0.30468;
	float temp3 = pow(fabs((CONST_A/(inCap[2]-122.07747))-1),1/1.16926)*0.30468;
	float temp4 = pow(fabs((CONST_A/(inCap[3]-122.07747))-1),1/1.16926)*0.30468;
	
	outL[0] = temp1;
	outL[1] = temp2;
	outL[2] = temp3;
	outL[3] = temp4;
}


void cal_QNM(float* inL,float* outM,float* outN,float* outQ)
{
	float M1 = (inL[2] - inL[1])*18;
	float M4 = (inL[2] - inL[1])*18;
	float M2 = (inL[3] - inL[0])*18;
	float M3 = (inL[3] - inL[0])*18;
	float N1 = (inL[1] - inL[0])*24;
	float N2 = (inL[1] - inL[0])*24;
	float N3 = (inL[2] - inL[3])*24;
	float N4 = (inL[2] - inL[3])*24;
	float Q1 = CONST_B*(inL[0]+inL[2])/2;
	float Q3 = CONST_B*(inL[0]+inL[2])/2;
	float Q2 = CONST_B*(inL[1]+inL[3])/2;
	float Q4 = CONST_B*(inL[1]+inL[3])/2;
	
	outM[0] = M1;
	outM[1] = M2;
	outM[2] = M3;
	outM[3] = M4;
	
	outN[0] = N1;
	outN[1] = N2;
	outN[2] = N3;
	outN[3] = N4;
	
	outQ[0] = Q1;
	outQ[1] = Q2;
	outQ[2] = Q3;
	outQ[3] = Q4;
}



void cal_d(float* inM,float* inN,float* inQ,float* outd,float* outj1,float* outj2)
{
	float d1,x,y,z,m,n,d2,d3,d4;
	x = pow(inM[0],2);
	y= pow(inN[0],2);
	z = pow(CONST_B,2);
	m = sqrt(x+y+z);
	n = inQ[0];
	
d1 = inQ[0]/sqrt((pow(inM[0],2) + pow(inN[0],2) + pow(CONST_B,2)));
d2 = inQ[1]/sqrt((pow(inM[1],2) + pow(inN[1],2) + pow(CONST_B,2)));
d3 = inQ[2]/sqrt((pow(inM[2],2) + pow(inN[2],2) + pow(CONST_B,2)));
d4 = inQ[3]/sqrt((pow(inM[3],2) + pow(inN[3],2) + pow(CONST_B,2)));
	
	float j11 =  atan(inN[0]/(-CONST_B)) * deg;
	float j12 =  atan(inN[1]/(-CONST_B)) * deg;
	float j13 =  atan(inN[2]/(-CONST_B)) * deg;
	float j14 =  atan(inN[3]/(-CONST_B)) * deg;
	
	float j21 = atan(inM[0]/(-CONST_B)) * deg;
	float j22 = atan(inM[1]/(-CONST_B)) * deg;
	float j23 = atan(inM[2]/(-CONST_B)) * deg;
	float j24 = atan(inM[3]/(-CONST_B)) * deg;
	
	outd[0] = d1;
	outd[1] = d2;
	outd[2] = d3;
	outd[3] = d4;
	
	outj1[0] = j11;
	outj1[1] = j12;
	outj1[2] = j13;
	outj1[3] = j14;
	
	outj2[0] = j21;
	outj2[1] = j22;
	outj2[2] = j23;
	outj2[3] = j24;
}