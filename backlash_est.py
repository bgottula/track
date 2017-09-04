#!/usr/bin/env python

# Adapted from an example here: http://docs.opencv.org/trunk/d7/d8b/tutorial_py_lucas_kanade.html

import config
import configargparse
import numpy as np
import cv2

parser = configargparse.ArgParser(default_config_files=config.DEFAULT_FILES)
parser.add_argument('--camera', help='device name of tracking camera', default='/dev/video0')
parser.add_argument('--scope', help='serial device for connection to telescope', default='/dev/ttyUSB0')
args = parser.parse_args()

mount = mounts.NexStarMount(args.scope)
position_start = mount.get_azalt()
deadband_az = 100.0
deadband_alt = 100.0
slew_rate = 100.0 / 3600.0

mount.slew('az', slew_rate)
mount.slew('alt', 0.0)
direction = 'right'

cap = cv2.VideoCapture(args.camera)

# params for ShiTomasi corner detection
feature_params = dict( maxCorners = 10,
                       qualityLevel = 0.2,
                       minDistance = 7,
                       blockSize = 15 )

# Parameters for lucas kanade optical flow
lk_params = dict( winSize  = (15,15),
                  maxLevel = 2,
                  criteria = (cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 10, 0.03))

# Create some random colors
color = np.random.randint(0,255,(100,3))

# Take first frame and find corners in it
ret, old_frame = cap.read()
old_gray = cv2.cvtColor(old_frame, cv2.COLOR_BGR2GRAY)
p0 = cv2.goodFeaturesToTrack(old_gray, mask = None, **feature_params)

# Create a mask image for drawing purposes
mask = np.zeros_like(old_frame)

while True:
    ret,frame = cap.read()
    if not ret:
        print('could not get frame')
        continue

    frame_gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)

    # calculate optical flow
    p1, st, err = cv2.calcOpticalFlowPyrLK(old_gray, frame_gray, p0, None, **lk_params)

    # Select good points
    if st is not None:
        good_new = p1[st==1]
        good_old = p0[st==1]

    # draw the tracks
    for i,(new,old) in enumerate(zip(good_new,good_old)):
        a,b = new.ravel()
        c,d = old.ravel()
        cv2.line(mask, (a,b),(c,d), color[i].tolist(), 2)
        cv2.circle(frame,(a,b),5,color[i].tolist(),-1)
    img = cv2.add(frame,mask)

    cv2.putText(img, "az deadband: " + str(deadband_az), (0, 15), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0,0,255))
    cv2.putText(img, "alt deadband: " + str(deadband_alt), (0, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0,0,255))
    cv2.putText(img, "slew rate: " + str(slew_rate * 3600.0), (0, 45), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0,0,255))

    cv2.imshow('frame',img)
    k = cv2.waitKey(30) & 0xff
    if k == 27:
        break
    elif k == ord('c'):
        mask = np.zeros_like(old_frame)
    elif k == ord('r'):
        mask = np.zeros_like(old_frame)
        old_gray = frame_gray.copy()
        p0 = cv2.goodFeaturesToTrack(old_gray, mask = None, **feature_params)
        continue
    elif k == ord('w'):
        deadband_alt += 1
    elif k == ord('s'):
        deadband_alt -= 1
    elif k == ord('a'):
        deadband_az -= 1
    elif k == ord('d'):
        deadband_az += 1
    elif k == ord('='):
        slew_rate += 10.0 / 3600.0
    elif k == ord('-'):
        slew_rate -= 10.0 / 3600.0

    # Now update the previous frame and previous points
    old_gray = frame_gray.copy()
    p0 = good_new.reshape(-1,1,2)

    position = mount.get_azalt()
    position_change = {
        'az': errorsources.wrap_error(position['az'] - position_start['az']) * 3600.0,
        'alt': errorsources.wrap_error(position['alt'] - position_start['alt']) * 3600.0,
    }
    print(str(position_change))
    if direction == 'right' and position_change['az'] > change_az:
        mount.slew('az', 0.0)
        mount.slew('alt', +slew_rate)
        direction = 'up'
    elif direction == 'up' and position_change['alt'] > change_alt:
        mount.slew('alt', 0.0)
        mount.slew('az', -slew_rate)
        direction = 'left'
    elif direction == 'left' and position_change['az'] <= 0.0:
        mount.slew('az', 0.0)
        mount.slew('alt', -slew_rate)
        direction = 'down'
    elif direction == 'down' and position_change['alt'] <= 0.0:
        mount.slew('alt', 0.0)
        mount.slew('az', +slew_rate)
        direction = 'right'

cv2.destroyAllWindows()
cap.release()
