import os, sys
import cv2
from skimage.data import coins
import matplotlib.pyplot as plt
import PyQt5
from PyQt5.QtWidgets import QWidget
import numpy as np

xtal = '/usr/local/XtalViewer/1.0/img/pID372_F4a.jpg'

#os.environ["QT_QPA_PLATFORM_PLUGIN_PATH"]= os.fspath(\
#    Path(PyQt5.__file__).resolve().parent /"Qt5" /"plugins")

"""
img = coins()
maxval = 255
thresh = maxval / 2


_, thresh1 = cv2.threshold(img, thresh, maxval, cv2.THRESH_BINARY)
_, thresh2 = cv2.threshold(img, thresh, maxval, cv2.THRESH_BINARY_INV)
_, thresh3 = cv2.threshold(img, thresh, maxval, cv2.THRESH_TRUNC)
_, thresh4 = cv2.threshold(img, thresh, maxval, cv2.THRESH_TOZERO)
_, thresh5 = cv2.threshold(img, thresh, maxval, cv2.THRESH_TOZERO_INV)

titles = ['orifinal', 'BINARY', 'BINARY_INV', 'TRUNC', 'TOZERO', 'TOZERO_INV']
images = [img, thresh1, thresh2, thresh3, thresh4, thresh5]

plt.figure(figsize=(9, 5))
for i in range(6):
    plt.subplot(2, 3, i+1), plt.imshow(images[i], 'gray')
    plt.title(titles[i], fontdict={'fontsize': 10})
    plt.axis('off')

plt.tight_layout(pad=0.7)
plt.show()
"""

img = cv2.imread(xtal,0)
"""
maxv = 255
thr = 150

ret, thresh1 = cv2.threshold(img,thr,maxv, cv2.THRESH_BINARY)
ret, thresh2 = cv2.threshold(img,thr,maxv, cv2.THRESH_BINARY_INV)
ret, thresh3 = cv2.threshold(img,thr,maxv, cv2.THRESH_TRUNC)
ret, thresh4 = cv2.threshold(img,thr,maxv, cv2.THRESH_TOZERO)
ret, thresh5 = cv2.threshold(img,thr,maxv, cv2.THRESH_TOZERO_INV)

titles =['Original','BINARY','BINARY_INV','TRUNC','TOZERO','TOZERO_INV']
images = [img,thresh1,thresh2,thresh3,thresh4,thresh5]

for i in range(6):
	plt.subplot(2,3,i+1),plt.imshow(images[i],'gray')
	plt.title(titles[i])
	plt.xticks([]),plt.yticks([])

plt.tight_layout(pad=0.7)
plt.show()

"""

maxval = 255
thresh = 100
ret, th1 = cv2.threshold(img, thresh, maxval, cv2.THRESH_BINARY)

k = 15
C = 10

th2 = cv2.adaptiveThreshold(
    img, maxval, cv2.ADAPTIVE_THRESH_MEAN_C, cv2.THRESH_BINARY, k, C)
th3 = cv2.adaptiveThreshold(
    img, maxval, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, k, C)

images = [img, th1, th2, th3]
titles = ['원본이미지', '임계처리', '평균 적응임계처리', '가우시안블러 적응임계처리']

plt.figure(figsize=(8, 5))
for i in range(4):
    plt.subplot(2, 2, i+1)
    plt.imshow(images[i], 'gray')
    plt.title(titles[i])
    plt.axis('off')

plt.tight_layout()
plt.show()
