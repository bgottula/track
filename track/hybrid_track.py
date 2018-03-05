#!/usr/bin/env python

"""Hybrid blind/optical tracking of objects.

This program uses a feedback control loop to cause a telescope mount to track an object based on
both predicted position and computer vision detection of the object with a camera. It includes
a union of the features in optical_track and blind_track. Transitions between blind and optical
tracking mode are handled automatically.
"""

from __future__ import print_function
import sys
import ephem
import track


def main():

    parser = track.ArgParser()
    parser.add_argument(
        '--camera',
        help='device node path for tracking webcam',
        default='/dev/video0')
    parser.add_argument(
        '--camera-res',
        help='webcam resolution in arcseconds per pixel',
        required=True,
        type=float)
    parser.add_argument(
        '--camera-bufs',
        help='number of webcam capture buffers',
        required=True,
        type=int)
    parser.add_argument(
        '--camera-exposure',
        help='webcam exposure level',
        default=2000,
        type=int)
    parser.add_argument(
        '--mount-type',
        help='select mount type (nexstar or gemini)',
        default='gemini'
    )
    parser.add_argument(
        '--mount-path',
        help='serial device node or hostname for mount command interface',
        default='/dev/ttyACM0'
    )
    parser.add_argument(
        '--lat',
        required=True,
        help='latitude of observer (+N)')
    parser.add_argument(
        '--lon',
        required=True,
        help='longitude of observer (+E)')
    parser.add_argument(
        '--elevation',
        required=True,
        help='elevation of observer (m)',
        type=float)
    parser.add_argument(
        '--loop-bw',
        help='control loop bandwidth (Hz)',
        default=0.5,
        type=float)
    parser.add_argument(
        '--loop-damping',
        help='control loop damping factor',
        default=2.0,
        type=float)
    parser.add_argument(
        '--backlash-az',
        help='backlash in azimuth (arcseconds)',
        default=0.0,
        type=float)
    parser.add_argument(
        '--backlash-alt',
        help='backlash in altitude (arcseconds)',
        default=0.0,
        type=float)
    parser.add_argument(
        '--max-divergence',
        help='max divergence of optical and blind sources (degrees)',
        default=2.0,
        type=float)
    parser.add_argument(
        '--align-dir-az',
        help='azimuth alignment approach direction (-1 or +1)',
        default=+1,
        type=int)
    parser.add_argument(
        '--align-dir-alt',
        help='altitude alignment approach direction (-1 or +1)',
        default=+1,
        type=int)

    subparsers = parser.add_subparsers(title='modes', dest='mode')

    parser_tle = subparsers.add_parser('tle', help='TLE file mode')
    parser_tle.add_argument(
        'file',
        help='filename of two-line element (TLE) target ephemeris')

    parser_coord = subparsers.add_parser('coord', help='fixed-body coordinate mode')
    parser_coord.add_argument(
        'ra',
        help='fixed body\'s right ascension',
        type=float)
    parser_coord.add_argument(
        'dec',
        help='fixed body\'s declination',
        type=float)

    parser_star = subparsers.add_parser('star', help='named star mode')
    parser_star.add_argument(
        'name',
        help='name of star')

    parser_star = subparsers.add_parser('solarsystem', help='named solar system body mode')
    parser_star.add_argument(
        'name',
        help='name of planet or moon')

    args = parser.parse_args()

    backlash_enable = args.backlash_az > 0.0 or args.backlash_alt > 0.0

    # Create object with base type TelescopeMount
    if args.mount_type == 'nexstar':
        mount = track.NexStarMount(args.mount_path)
        if backlash_enable:
            mount.set_backlash('az', args.align_dir_az, args.backlash_az / 3600.0)
            mount.set_backlash('alt', args.align_dir_alt, args.backlash_alt / 3600.0)
    elif args.mount_type == 'gemini':
        mount = track.LosmandyGeminiMount(args.mount_path)
    else:
        print('mount-type not supported: ' + args.mount_type)
        sys.exit(1)

    # Create a PyEphem Observer object
    observer = ephem.Observer()
    observer.lat = args.lat
    observer.lon = args.lon
    observer.elevation = args.elevation

    # Create a PyEphem Body object corresonding to the TLE file
    if args.mode == 'tle':
        print('In TLE file mode: \'{}\'.'.format(args.file))
        tle = []
        with open(args.file) as tlefile:
            for line in tlefile:
                tle.append(line)
        target = ephem.readtle(tle[0], tle[1], tle[2])

    # Create a PyEphem Body object corresonding to the given fixed coordinates
    if args.mode == 'coord':
        print('In fixed-body coordinate mode: (RA {}, dec {}).'.format(args.ra, args.dec))
        target = ephem.FixedBody(_ra=args.ra, _dec=args.dec)

    # Get the PyEphem Body object corresonding to the given named star
    if args.mode == 'star':
        print('In named star mode: \'{}\''.format(args.name))
        target = ephem.star(args.name)

    # Get the PyEphem Body object corresonding to the given named solar system body
    if args.mode == 'solarsystem':
        print('In named solar system body mode: \'{}\''.format(args.name))
        ss_objs = [name for _, _, name in ephem._libastro.builtin_planets()]
        if args.name in ss_objs:
            body_type = getattr(ephem, args.name)
            target = body_type()
        else:
            raise Exception(
                'The solar system body \'{}\' isn\'t present in PyEphem.'.format(
                    args.name))

    # Create object with base type ErrorSource
    error_source = track.HybridErrorSource(
        mount,
        observer,
        target,
        args.camera,
        args.camera_res,
        args.camera_exposure,
        args.max_divergence,
        backlash_enable
    )

    try:
        # Create gamepad object and register callback
        game_pad = track.Gamepad(
            left_gain=2.0,  # left stick degrees per second
            right_gain=0.5,  # right stick degrees per second
            int_limit=5.0,  # max correction in degrees for either axis
        )
        game_pad.integrator_mode = True
        error_source.register_blind_offset_callback(game_pad.get_integrator)
        print('Gamepad found and registered.')
    except RuntimeError:
        print('No gamepads found.')

    tracker = track.Tracker(
        mount=mount,
        error_source=error_source,
        loop_bandwidth=args.loop_bw,
        damping_factor=args.loop_damping
    )

    try:
        tracker.run()
    except KeyboardInterrupt:
        print('Goodbye!')


if __name__ == "__main__":
    main()
