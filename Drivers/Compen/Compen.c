#include "Compen.h"
// 补偿设置数组
const ComPenSet compensets[4] = {
    {
        {  // C01补偿系数
            {0.99990453,0.00000326,0.99999790,0.00011104,0.99999566,0.05362701,0.99900119,0.03531138,0.999,0.003,0.990,0.050,0.89,0.10,0.99,0.02},      // Z轴系数
            {1,0,1,0,1,0,1, 0,1, 0,1 ,0,1, 0,1, 0}, // Fy轴系数
            {1,0,1,0,1,0,1, 0,1, 0,1 ,0,1, 0,1, 0}  // Fx轴系数
        }
    },
    {
        {  // C02补偿系数
            {0.99999236, 0.00000132, 0.99992184, 0.00168657, 0.99999606, 0.05030534, 0.99966560, 0.05911378, 0.997, 0.05, 0.99, 0.001, 0.99, 0.05, 0.97, 0.09},      // Z轴系数
            {1,0,1,0,1,0,1, 0,1, 0,1 ,0,1, 0,1, 0}, // Fy轴系数
            {1,0,1,0,1,0,1, 0,1, 0,1 ,0,1, 0,1, 0}  // Fx轴系数
        }
    },
    {
        {  // C03补偿系数
            {0.99985, 0.035, 0.9975, 0.020, 0.99, 0.015},      // Z轴系数
            {0.99985, 0.035, 0.9975, 0.020, 0.99, 0.015}, // My轴系数
            {0.99985, 0.035, 0.9975, 0.020, 0.99, 0.015}  // Mx轴系数
        }
    },
    {
        {  // C04补偿系数
            {0.99975, 0.027, 0.998, 0.02, 0.99, 0.015},      // Z轴系数
            {0.99985, 0.035, 0.9975, 0.020, 0.99, 0.015}, // My轴系数
            {0.99985, 0.035, 0.9975, 0.020, 0.99, 0.015}  // Mx轴系数
        }
    }
};

//G 温度补偿系数
const Coefficients coeffs[4][5] = {
    {    /*p 传感器1的系数 */
        {-127.9, -11.43, 1.045, 0.002475, -0.0001638, -7.616e-06},  /*p Z */
        {0, 0, 1, 0, 0, 0},                /*p MY */
        {0, 0, 1, 0, 0, 0},                /*p MX */
    },
    {    /*p 传感器2的系数 */
        {0, 0, 1, 0, 0, 0},   /*p Z */
        {0, 0, 1, 0, 0, 0},                  /*p MY */
        {0, 0, 1, 0, 0, 0},                 /*p MX */
    },
    {   /*p 传感器3的系数 */
        {0, 0, 1, 0, 0, 0},  /*p Z */
        {0, 0, 1, 0, 0, 0},                /*p MY */
        {0, 0, 1, 0, 0, 0},                /*p MX */
    },
    {   /*p 传感器4的系数 */
        {0, 0, 1, 0, 0, 0},  /*p Z */
        {0, 0, 1, 0, 0, 0},                 /*p MY */
        {0, 0, 1, 0, 0, 0},                 /*p MX */
    }
};

/*******************************************************************************************
 * 函数功能  温度补偿
 *
 * @idea 该函数根据输入的 standardCap 和 calibration 值，利用传感器索引和轴索引确定对应的二次多项式系数集，
 *       然后根据这些系数对输入数据进行二次多项式运算，输出一个 16 位整数结果。
 * @param standardCap 输入的传感器 的标准电容的值
 * @param capIn 输入的传感器的待校准电容值
 * @param sensor_index 传感器索引，范围为 0 到 3，确定使用哪个传感器的系数
 * @param axis_index 轴索引，范围为 0 到 4，确定使用该传感器哪个方向的系数
 * @return INT16 返回通过二次多项式计算得出的结果
 * @see 无
 * @note 传感器索引和轴索引必须在有效范围内，否则返回 0
 *******************************************************************************************/

int16_t TemperatureCompen(int16_t standardCap, int16_t* capIn, int sensorIndex, int16_t* resultTemp) {
    /*无效索引*/
    if (sensorIndex < 0 || sensorIndex >= 4) {
        return 0; 
    }

    int i;

    for(i = 0; i <= 2; i++) { 
		const Coefficients *p = &coeffs[sensorIndex][i]; 
        resultTemp[i] = p->p00 + p->p10 * standardCap + p->p01 * capIn[i] +
              p->p20 * standardCap * standardCap + p->p11 * standardCap * capIn[i] + p->p02 * capIn[i] * capIn[i];
    }
    return 1;
} 

// 滞后补偿设置数组
const HysteresisSet hysteresisSets[4] = {
    {
        {
            {0, 0, 0},  // C01滞后补偿系数
            // 其他系数
        }
    },
    {
        {
            {-0.0000274144967875, 0.10862949, 10.59570396},  // C02滞后补偿系数
            // 其他系数
        }
    },
    {
        {
            {-0.0000205287212012, 0.125697459702374, -9.8644237178},  // C03滞后补偿系数
            // 其他系数
        }
    },
    {
        {
            {0, 0, 0},  // C04滞后补偿系数
            // 其他系数
        }
    },
};
/**
 * @brief 计算滞后补偿后的值
 * 
 * 根据滞后系数对输入值进行补偿
 *
 * @param value 要计算的输入值
 * @param coeffs 滞后系数
 * @return 补偿后的值为 int16_t
 */
int16_t calculateHysteresisCompensation(int16_t value, HysteresisCoefficients coeffs) {
    return (int16_t)(coeffs.quadraticMultiplier * value * value +
                     coeffs.linearMultiplier * value +
                     coeffs.offset);
}

/*******************************************************************************************
 * 函数功能  动态补偿
 *
 * @idea 该函数根据提供的补偿系数，对输入的待补偿电容数组进行补偿。补偿结果存储在 comCap 中
 * @param *rawCap 待补偿电容数组
 * @param *comCap 动态补偿后数组
 * @param setIndex 传感器标号
 * @return 无
 * @see 无
 * @note 补偿是基于输入的原始力和历史力差值计算的，并结合了多项补偿系数，最终结果存储在 comCap 中

 *******************************************************************************************/
 double RawForceZ = 0, RawForceM1 = 0, RawForceM2 = 0;   //v 原始力 Z, My, Mx
    double TxForce[3] = {0};
    double diffZ = 0;
    double diffM1 = 0;
    double diffM2 = 0;
void CompenFunction(int16_t *rawCap, int16_t *comCap, int setIndex,int zeroFlag)
{

    static double COEZp[4][10] = {0};
    static double COEM1p[4][10] = {0};
    static double COEM2p[4][10] = {0};

    static double RawForceOldZ[3] = {0,0,0}, RawForceOldM1[3] = {0,0,0}, RawForceOldM2[3] = {0,0,0}; //v 原始力历史值

    int i = 0;

    if((zeroFlag) == 1)
    {
        for (i=0; i < 4; i++) {
            COEZp[setIndex][i] = 0;
            COEM1p[setIndex][i] = 0;
            COEM2p[setIndex][i] = 0;

            RawForceOldZ[setIndex] = 0;
            RawForceOldM1[setIndex] = 0;
            RawForceOldM2[setIndex] = 0;
        }
        return;

    }

    RawForceZ = rawCap[0];   /*p 原始力 Z */
    RawForceM1 = rawCap[1];  /*p 原始力 FY */
    RawForceM2 = rawCap[2];  /*p原始力 FX */

    ComPenSet selectsets = compensets[setIndex];

    diffZ = RawForceZ - RawForceOldZ[setIndex];
    diffM1 = RawForceM1 - RawForceOldM1[setIndex];
    diffM2 = RawForceM2 - RawForceOldM2[setIndex];

    RawForceOldZ[setIndex] = RawForceZ;
    RawForceOldM1[setIndex] = RawForceM1;
    RawForceOldM2[setIndex] = RawForceM2;

    /*p Z方向补偿*/
    COEZp[setIndex][0] = COEZp[setIndex][0] * selectsets.coefficients[0].num1 + diffZ * selectsets.coefficients[0].num2;
    COEZp[setIndex][1] = COEZp[setIndex][1] * selectsets.coefficients[0].num3 + diffZ * selectsets.coefficients[0].num4;
    COEZp[setIndex][2] = COEZp[setIndex][2] * selectsets.coefficients[0].num5 + diffZ * selectsets.coefficients[0].num6;
		COEZp[setIndex][3] = COEZp[setIndex][3] * selectsets.coefficients[0].num7 + diffZ * selectsets.coefficients[0].num8;
		COEZp[setIndex][4] = COEZp[setIndex][4] * selectsets.coefficients[0].num9 + diffZ * selectsets.coefficients[0].num10;
		COEZp[setIndex][5] = COEZp[setIndex][5] * selectsets.coefficients[0].num11 + diffZ * selectsets.coefficients[0].num12;
		COEZp[setIndex][6] = COEZp[setIndex][6] * selectsets.coefficients[0].num13 + diffZ * selectsets.coefficients[0].num14;
		COEZp[setIndex][7] = COEZp[setIndex][7] * selectsets.coefficients[0].num15 + diffZ * selectsets.coefficients[0].num16;
		
		

    TxForce[0] = RawForceZ + COEZp[setIndex][0] + COEZp[setIndex][1] + COEZp[setIndex][2] + COEZp[setIndex][3] + COEZp[setIndex][4] + COEZp[setIndex][5] + COEZp[setIndex][6] + COEZp[setIndex][7] ;

    /*p My方向补偿*/
    COEM1p[setIndex][0] = COEM1p[setIndex][0] * selectsets.coefficients[1].num1 + diffM1 * selectsets.coefficients[1].num2;
    COEM1p[setIndex][1] = COEM1p[setIndex][1] * selectsets.coefficients[1].num3 + diffM1 * selectsets.coefficients[1].num4;
    COEM1p[setIndex][2] = COEM1p[setIndex][2] * selectsets.coefficients[1].num5 + diffM1 * selectsets.coefficients[1].num6;
		COEM1p[setIndex][3] = COEM1p[setIndex][3] * selectsets.coefficients[1].num7 + diffM1 * selectsets.coefficients[1].num8;
		COEM1p[setIndex][4] = COEM1p[setIndex][4] * selectsets.coefficients[1].num9 + diffM1 * selectsets.coefficients[1].num10;
		COEM1p[setIndex][5] = COEM1p[setIndex][5] * selectsets.coefficients[1].num11 + diffM1 * selectsets.coefficients[1].num12;
		COEM1p[setIndex][6] = COEM1p[setIndex][6] * selectsets.coefficients[1].num13 + diffM1 * selectsets.coefficients[1].num14;
		COEM1p[setIndex][7] = COEM1p[setIndex][7] * selectsets.coefficients[1].num15 + diffM1 * selectsets.coefficients[1].num16;

    TxForce[1] = RawForceM1 + COEM1p[setIndex][0] + COEM1p[setIndex][1] + COEM1p[setIndex][2] + COEM1p[setIndex][3] + COEM1p[setIndex][4] + COEM1p[setIndex][5] + COEM1p[setIndex][6] + COEM1p[setIndex][7] ;

    /*p Mx方向补偿*/
    COEM2p[setIndex][0] = COEM2p[setIndex][0] * selectsets.coefficients[2].num1 + diffM2 * selectsets.coefficients[2].num2;
    COEM2p[setIndex][1] = COEM2p[setIndex][1] * selectsets.coefficients[2].num3 + diffM2 * selectsets.coefficients[2].num4;
    COEM2p[setIndex][2] = COEM2p[setIndex][2] * selectsets.coefficients[2].num5 + diffM2 * selectsets.coefficients[2].num6;
		COEM2p[setIndex][3] = COEM2p[setIndex][3] * selectsets.coefficients[2].num7 + diffM2 * selectsets.coefficients[2].num8;
		COEM2p[setIndex][4] = COEM2p[setIndex][4] * selectsets.coefficients[2].num9 + diffM2 * selectsets.coefficients[2].num10;
		COEM2p[setIndex][5] = COEM2p[setIndex][5] * selectsets.coefficients[2].num11 + diffM2 * selectsets.coefficients[2].num12;
		COEM2p[setIndex][6] = COEM2p[setIndex][6] * selectsets.coefficients[2].num13 + diffM2 * selectsets.coefficients[2].num14;
		COEM2p[setIndex][7] = COEM2p[setIndex][7] * selectsets.coefficients[2].num15 + diffM2 * selectsets.coefficients[2].num16;


    TxForce[2] = RawForceM2 + COEM2p[setIndex][0] + COEM2p[setIndex][1] + COEM2p[setIndex][2] + COEM2p[setIndex][3] + COEM2p[setIndex][4] + COEM2p[setIndex][5] + COEM2p[setIndex][6] + COEM2p[setIndex][7] ;

    /*p 补偿后的力  Z, My, Mx */
    comCap[0] = (int16_t)TxForce[0];
    comCap[1] = (int16_t)TxForce[1];
    comCap[2] = (int16_t)TxForce[2];
}
/*******************************************************************************************
 * 函数功能  计算迟滞补偿值
 *
 * @idea 该函数根据提供的滞后系数计算滞后补偿值。补偿值通过输入值的平方项、线性项和偏移项计算得出
 * @param value 输入的原始值
 * @param coeffs 包含滞后补偿系数的结构体
 * @return 返回值 int16_t 返回计算后的滞后补偿值
 * @see 无
 * @note 使用输入值的平方和线性关系，以及偏移量进行补偿计算
 *******************************************************************************************/
int16_t CalculateHysteresisCompensation(int16_t value, HysteresisCoefficients coeffs) {
    return (int16_t)(coeffs.quadraticMultiplier * value * value +
                     coeffs.linearMultiplier * value +
                     coeffs.offset); /*p补偿值通过输入值的平方项、线性项和偏移项计算得出*/
}

/**
 * @brief 滤波处理
 *
 * 对输入数据进行滤波
 *
 * @param Data 输入数据数组
 * @param size 数据大小
 * @param AFA 滤波系数
 */
void filter(int16_t * Data, size_t size, float AFA) {
    if (size < 2) {
        return; // 数据太小
    }

    static int16_t filter_out[8] = {0};

    for (int i = 0; i < size; ++i) {
        filter_out[i] = filter_out[i] + AFA * (Data[i] - filter_out[i]);
        Data[i] = filter_out[i]; // 更新滤波后的值
    }
}
