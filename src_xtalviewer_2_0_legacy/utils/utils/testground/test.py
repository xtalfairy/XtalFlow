
import numpy as np
import cv2

# Read input image
src_im = cv2.imread('fairy_03.png')

# Build a transformation matrix (the transformation matrix is transposed relative to MATLAB)
t = np.float32([[1, -1, 0],
                [0,  1, 0],
                [0,  0, 1]])

# Use only first two rows (affine transformation assumes last row is [0, 0, 1])
#trans = np.float32([[1, -1, 0],
#                    [0,  1, 0]])
trans = t[0:2, :]

inv_t = np.linalg.inv(t)
inv_trans = inv_t[0:2, :]

# get the sizes
h, w = src_im.shape[:2]

# Transfrom the 4 corners of the input image
src_pts = np.float32([[0, 0], [w-1, 0], [0, h-1], [w-1, h-1]]) # https://stackoverflow.com/questions/44378098/trouble-getting-cv-transform-to-work (see comment).
dst_pts = cv2.transform(np.array([src_pts]), trans)[0]

min_x, max_x = np.min(dst_pts[:, 0]), np.max(dst_pts[:, 0])
min_y, max_y = np.min(dst_pts[:, 1]), np.max(dst_pts[:, 1])

# Destination matrix width and height
dst_w = int(max_x - min_x + 1) # 895
dst_h = int(max_y - min_y + 1) # 384

# Inverse transform the center of destination image, for getting the coordinate on the source image.
dst_center = np.float32([[(dst_w-1.0)/2, (dst_h-1.0)/2]])
src_projected_center = cv2.transform(np.array([dst_center]), inv_trans)[0]

# Compute the translation of the center - assume source center goes to destination center
translation = src_projected_center - np.float32([[(w-1.0)/2, (h-1.0)/2]])

# Place the translation in the third column of trans
trans[:, 2] = translation

# Transform
dst_im = cv2.warpAffine(src_im, trans, (dst_w, dst_h))

# Show dst_im as output
cv2.imshow('dst_im', dst_im)
cv2.waitKey()
cv2.destroyAllWindows()

# Store output for testing
cv2.imwrite('dst_im.png', dst_im)
