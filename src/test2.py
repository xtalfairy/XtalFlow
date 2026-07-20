import cv2
import numpy as np
#xtal = '/usr/local/XtalViewer/1.0/img/pID372_F4a.jpg'
xtal = '/smbmount/RockMakerStorage/WellImages/318/plateID_318/batchID_89/wellNum_2/profileID_1/d1_r1244_ef.jpg'

#img = cv2.imread('./data/opencv_logo.png',0)
img = cv2.imread(xtal)


gray = cv2.cvtColor(img,cv2.COLOR_BGR2GRAY)
edges = cv2.Canny(gray,50,150,apertureSize = 3)

lines = cv2.HoughLines(edges,1,np.pi/180,150)
for line in lines:
    rho,theta = line[0]
    a = np.cos(theta)
    b = np.sin(theta)
    x0 = a*rho
    y0 = b*rho
    x1 = int(x0 + 1000*(-b))
    y1 = int(y0 + 1000*(a))
    x2 = int(x0 - 1000*(-b))
    y2 = int(y0 - 1000*(a))

    cv2.line(img,(x1,y1),(x2,y2),(0,0,255),1)

cv2.imshow('edges', edges)
cv2.imshow('result', img)
cv2.waitKey()
cv2.destroyAllWindows()
