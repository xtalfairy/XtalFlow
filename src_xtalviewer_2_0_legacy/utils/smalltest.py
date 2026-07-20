import numpy as np
import math


def cosarr(arr):
    cosA = np.zeros_like(arr, dtype = 'float32')
    for i,row in enumerate(arr):
        for j,col in enumerate(row):
            rad = col
            deg = rad * (math.pi*2/360)
            print(deg)
            cosA[i, j] = math.cos(deg)
    return cosA

arr = np.array([[1,2,3,4],[5,6,7,8],[9,10,11,12],[13,14,0,16]])
print(cosarr(arr))
