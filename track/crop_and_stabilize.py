#!/usr/bin/env python

# For all JPEG images in a directory, centers on a bright blob and then crops
# the image with that blob in the center. Places the new cropped image in
# a subdirectory cropped/.

import sys
import os
import cv2
import numpy as np

pixels_from_center = 200

# initialize blob detector
params = cv2.SimpleBlobDetector_Params()
params.filterByColor = False
params.filterByConvexity = False
params.filterByInertia = False
params.maxArea = 50000.0
params.minThreshold = 1
params.maxThreshold = 255
params.minDistBetweenBlobs = 200
blob_detector = cv2.SimpleBlobDetector_create(params)

input_dir = os.path.normpath(sys.argv[1]) + os.sep
output_dir = input_dir + 'cropped/'

if not os.path.exists(output_dir):
    os.makedirs(output_dir)

cropped_num = 0
for filename in sorted(os.listdir(input_dir)):
    if filename.endswith('JPG') == False:
        continue
    print(filename)
    img = cv2.imread(input_dir + filename)
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    keypoints = blob_detector.detect(gray)
    if len(keypoints) != 1:
        print('found ' + str(len(keypoints)) + ' features in ' + filename)
        continue
    center = keypoints[0].pt
    img_cropped = img[int(center[1])-pixels_from_center:int(center[1])+pixels_from_center, int(center[0])-pixels_from_center:int(center[0])+pixels_from_center]
    cv2.imwrite(output_dir + '%04d.jpg' % cropped_num, img_cropped)
    cropped_num += 1