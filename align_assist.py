#!/usr/bin/env python

import track
import mounts
import errorsources
import argparse
import sys
import time


parser = argparse.ArgumentParser()
parser.add_argument('--camera', help='device name of tracking camera', default='/dev/video0')
parser.add_argument('--camera-res', help='camera resolution in arcseconds per pixel', required=True, type=float)
parser.add_argument('--scope', help='serial device for connection to telescope', default='/dev/ttyUSB0')
parser.add_argument('--loop-bw', help='control loop bandwidth (Hz)', default=0.1, type=float)
parser.add_argument('--loop-damping', help='control loop damping factor', default=2.0, type=float)
parser.add_argument('--loop-period', help='control loop period', default=0.5, type=float)
parser.add_argument('--backlash-az', help='backlash in azimuth (arcseconds)', default=0.0, type=float)
parser.add_argument('--backlash-alt', help='backlash in altitude (arcseconds)', default=0.0, type=float)
parser.add_argument('--align-dir-az', help='azimuth alignment approach direction (-1 or +1)', default=+1, type=int)
parser.add_argument('--align-dir-alt', help='altitude alignment approach direction (-1 or +1)', default=+1, type=int)
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

def stop_at_half_frame_callback():
    if (abs(tracker.error['az']) > HALF_FRAME_ERROR_MAG or 
        abs(tracker.error['alt']) > HALF_FRAME_ERROR_MAG):
            tracker.stop = True

def stop_at_frame_edge_callback():
    if (abs(tracker.error['az']) > NEAR_FRAME_EDGE_ERROR_MAG or 
        abs(tracker.error['alt']) > NEAR_FRAME_EDGE_ERROR_MAG):
            tracker.stop = True

def error_print_callback():
    print('\terror (az,alt): ' + str(tracker.error['az'] * 3600.0) + ', ' + str(tracker.error['alt'] * 3600.0))

def stop_beyond_deadband_callback():
    position_stop = mount.get_azalt(remove_backlash=False)
    position_change = abs(errorsources.wrap_error(position_stop[other_axis] - position_start[other_axis]))
    if position_change >= 1.25 * backlash:
        tracker.stop = True

try:

    # Some of these constants make assumptions about specific hardware
    SLEW_STOP_SLEEP = 3.0
    SLOW_SLEW_RATE = 30.0 / 3600.0
    # If the magnitude of the error is larger than this value, the object
    # is more than 50% of the distance from the center of the frame to the
    # nearest edge.
    HALF_FRAME_ERROR_MAG = 0.5 * 0.5 * error_source.degrees_per_pixel * min(
        error_source.frame_height_px, 
        error_source.frame_width_px
    )

    # If the magnitude of the error is larger than this value, the object
    # is more than 80% of the distance from the center of the frame to the
    # nearest edge.
    NEAR_FRAME_EDGE_ERROR_MAG = 0.8 * 0.5 * error_source.degrees_per_pixel * min(
        error_source.frame_height_px, 
        error_source.frame_width_px
    )

    # determine which axes are slewing in the same direction as the desired 
    # approach direction
    print('Centering object and measuring tracking slew rates and directions...')
    tracker.run()
    rates = tracker.slew_rate
    track_axes = []
    if ((rates['az'] > 0.0 and args.align_dir_az == +1) or
        (rates['az'] < 0.0 and args.align_dir_az == -1)):
        track_axes.append('az')
    if ((rates['alt'] > 0.0 and args.align_dir_alt == +1) or
        (rates['alt'] < 0.0 and args.align_dir_alt == -1)):
        track_axes.append('alt')
    print('\taz rate: ' + str(rates['az'] * 3600.0) + ' arcseconds/s')
    print('\talt rate: ' + str(rates['alt'] * 3600.0) + ' arcseconds/s')
    print('\taxes tracking in approach direction: ' + str(track_axes))

    # case a: Slewing in the desired approach direction in both axes to track
    # object. Just keep doing this indefinitely.
    if len(track_axes) == 2:
        tracker.run(track_axes)

    # case b: One axis is slewing in the desired approach direction but the 
    # other is not.
    elif len(track_axes) == 1:

        other_axis = 'az' if 'alt' in track_axes else 'alt'
        align_dir = args.align_dir_az if other_axis == 'az' else args.align_dir_alt
        backlash = (args.backlash_az if other_axis == 'az' else args.backlash_alt) / 3600.0

        # move object away from center of frame such that its apparent sidereal
        # motion will be towards center
        print('Moving object near edge of frame...')
        mount.slew(other_axis, SLOW_SLEW_RATE * -align_dir)
        tracker.register_callback(stop_at_frame_edge_callback)
        tracker.run(track_axes)
        tracker.register_callback(None)

        # slew in desired approach direction until backlash is removed
        print('Slewing in approach direction past backlash deadband...')
        position_start = mount.get_azalt(remove_backlash=False)
        mount.slew(other_axis, SLOW_SLEW_RATE * align_dir)
        tracker.register_callback(stop_beyond_deadband_callback)
        tracker.run(track_axes)
        tracker.register_callback(None)

        # wait for object to drift back towards center.
        print('Press ALIGN on hand controller when object crosses frame center...')
        mount.slew(other_axis, 0.0)
        tracker.register_callback(error_print_callback)
        tracker.run(track_axes)
        tracker.register_callback(None)

    # case c: Neither axis is slewing in the desired approach direction
    else:
        print('neither axis is tracking in the approach direction -- this case is not implemented!')
        sys.exit(1)

except KeyboardInterrupt:
    print('Goodbye!')
    pass