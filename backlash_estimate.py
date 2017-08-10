#!/usr/bin/env python

import track
import mounts
import errorsources
import argparse
import time
import numpy as np

parser = argparse.ArgumentParser()
parser.add_argument('--camera', help='device name of tracking camera', default='/dev/video0')
parser.add_argument('--camera-res', help='camera resolution in arcseconds per pixel', required=True, type=float)
parser.add_argument('--scope', help='serial device for connection to telescope', default='/dev/ttyUSB0')
parser.add_argument('--loop-bw', help='control loop bandwidth (Hz)', default=0.1, type=float)
parser.add_argument('--loop-damping', help='control loop damping factor', default=2.0, type=float)
parser.add_argument('--loop-period', help='control loop period', default=0.5, type=float)
args = parser.parse_args()

# Create object with base type TelescopeMount
mount = mounts.NexStarMount(args.scope)

# Create object with base type ErrorSource
error_source = errorsources.OpticalErrorSource(args.camera, args.camera_res)

tracker = track.TrackUntilConverged(
    mount = mount, 
    error_source = error_source, 
    update_period = args.loop_period,
    loop_bandwidth = args.loop_bw,
    damping_factor = args.loop_damping
)

error_lists = {'az': [], 'alt': []}

def tracker_callback():
    error_lists['az'].append(tracker.error['az'])
    error_lists['alt'].append(tracker.error['alt'])

def stop_at_half_frame_callback():
    if (abs(tracker.error['az']) > HALF_FRAME_ERROR_MAG or 
        abs(tracker.error['alt']) > HALF_FRAME_ERROR_MAG):
            tracker.stop = True

try:

    SLEW_STOP_SLEEP = 3.0
    SLOW_SLEW_RATE = 15.0 / 3600.0
    NUM_ITERATIONS = 5
    # If the magnitude of the error is larger than this value, the object
    # is more than 50% of the distance from the center of the frame to the
    # nearest edge.
    HALF_FRAME_ERROR_MAG = 0.5 * 0.5 * error_source.degrees_per_pixel * min(
        error_source.frame_height_px, 
        error_source.frame_width_px
    )

    backlash_estimates = {}
    for axis in ['az', 'alt']:

        backlash_estimates[axis] = []
        other_axis = 'az' if axis == 'alt' else 'alt'

        for i in range(NUM_ITERATIONS):

            print('Start iteration ' + str(i) + ' of ' + str(NUM_ITERATIONS))
            print('Centering object in FOV...')

            # center the object in the FOV
            tracker.run()

            # Continue tracking for a bit to estimate variance of object's
            # apparent position due to mount jitter, seeing, tracking loop 
            # noise, and other effects. Also estimate the slew rate of the
            # mount.
            print('Estimating steady state tracking variance...')
            error_lists['az'] = []
            error_lists['alt'] = []
            position_start = mount.get_azalt()
            time_start = time.time()
            tracker.register_callback(tracker_callback)
            tracker.run()
            tracker.register_callback(None)
            tracking_mean = {
                'az': np.mean(error_lists['az']),
                'alt': np.mean(error_lists['alt'])
            }
            tracking_sigma = {
                'az': np.std(error_lists['az']),
                'alt': np.std(error_lists['alt'])
            }
            position_stop = mount.get_azalt()
            time_elapsed = time.time() - time_start
            slew_rate_est = {
                'az': (position_stop['az'] - position_start['az']) / time_elapsed,
                'alt': (position_stop['alt'] - position_start['alt']) / time_elapsed
            }
            print('\taz tracking mean (arcseconds): ' + str(tracking_mean['az'] * 3600.0))
            print('\talt tracking mean (arcseconds): ' + str(tracking_mean['alt'] * 3600.0))
            print('\taz tracking sigma (arcseconds): ' + str(tracking_sigma['az'] * 3600.0))
            print('\talt tracking sigma (arcseconds): ' + str(tracking_sigma['alt'] * 3600.0))
            print('\taz slew rate (arcsec/s) ' + str(slew_rate_est['az'] * 3600.0) + ', ' + str(tracker.loop_filter['az'].int * 3600.0))
            print('\talt slew rate (arcsec/s): ' + str(slew_rate_est['alt'] * 3600.0) + ', ' + str(tracker.loop_filter['alt'].int * 3600.0))

            # estimate object's apparent motion with mount stationary in axis
            # under test but continuing to slew in other axis
            print('Estimating object apparent motion while tracking in single axis...')
            mount.slew(axis, 0.0)
            time.sleep(SLEW_STOP_SLEEP)
            error_start = error_source.compute_error()
            time_start = time.time()
            tracker.register_callback(stop_at_half_frame_callback)
            tracker.run(axes=[other_axis])
            tracker.register_callback(None)
            error_stop = error_source.compute_error()
            time_elapsed = time.time() - time_start
            apparent_motion = {
                'az': (error_stop['az'] - error_start['az']) / time_elapsed,
                'alt': (error_stop['alt'] - error_start['alt']) / time_elapsed
            }
            print('\taz apparent motion (arcsec/s): ' + str(apparent_motion['az'] * 3600.0))
            print('\talt apparent motion (arcsec/s): ' + str(apparent_motion['alt'] * 3600.0))

            # Compute ratio of sidreal rate estimated by the camera to the slew
            # rate of the mount in the axis under consideration. Hopefully this
            # is near 1.0, but in practice it may be a little bit off.
            camera_to_mount_rate = np.absolute(apparent_motion['az'] + 1j*apparent_motion['alt']) / slew_rate_est[axis]
            print('\tcamera to mount rate ratio: ' + str(camera_to_mount_rate))

            # center the object in the FOV
            print('Tracking object until converged...')
            tracker.run()

            # slew opposite tracking direction until object is near edge of frame
            print('Slewing in opposite of tracking direction in ' + str(axis) + '...')
            error_start = error_source.compute_error()
            position_start = mount.get_azalt()
            time_start = time.time()
            mount.slew(axis, -mount.last_slew_dir[axis] * SLOW_SLEW_RATE)
            tracker.register_callback(stop_at_half_frame_callback)
            tracker.run(axes=[other_axis])
            tracker.register_callback(None)
            error_stop = error_source.compute_error()
            position_stop = mount.get_azalt()
            time_elapsed = time.time() - time_start

            # calculations to estimate backlash deadband
            uncorrected_motion = {
                'az': error_stop['az'] - error_start['az'],
                'alt': error_stop['alt'] - error_start['alt'],
            }
            predicted_sidereal_motion = {
                'az': apparent_motion['az'] * time_elapsed,
                'alt': apparent_motion['alt'] * time_elapsed,
            }
            corrected_motion = {
                'az': uncorrected_motion['az'] - predicted_sidereal_motion['az'],
                'alt': uncorrected_motion['alt'] - predicted_sidereal_motion['alt'],
            }
            mount_pos_change_predict = np.absolute(corrected_motion['az'] + 1j*corrected_motion['alt']) / camera_to_mount_rate
            mount_pos_change = abs(position_stop[axis] - position_start[axis])
            backlash_estimates[axis].append(abs(mount_pos_change - mount_pos_change_predict))
            print('\telapsed time: ' + str(time_elapsed))
            print('\tuncorrected motion: ' + str(uncorrected_motion))
            print('\tpredicted sidereal motion: ' + str(predicted_sidereal_motion))
            print('\tcorrected motion: ' + str(corrected_motion))
            print('\tpredicted change in mount position: ' + str(mount_pos_change_predict))
            print('\tmount reported change in position: ' + str(mount_pos_change))

            print('Estimate of backlash deadband is ' + str(backlash_estimates[axis][-1] * 3600.0) + ' arcseconds.')

        print('Iterations for ' + axis + ' axis completed.')
        print('Mean backlash: ' + str(np.mean(backlash_estimates[axis]) * 3600.0) + ' arcseconds')
        print('Standard deviation: ' + str(np.std(backlash_estimates[axis]) * 3600.0) + ' arcseconds')

    # stop the mount
    mount.slew('az', 0.0)
    mount.slew('alt', 0.0)

except KeyboardInterrupt:
    print('Goodbye!')
    pass
