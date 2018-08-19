#!/usr/bin/env python3

"""Blind tracking of objects.

This program uses a feedback control loop to cause a telescope mount to track an object based on
the object's predicted position in the sky and the mount's self-reported position. It is called
"blind" tracking because it does not rely on any direct measurement or observation of the object
to function; it relies entirely on the accuracy of the mount's alignment and on the accuracy of
the predicted position.

The object may be anything supported by the PyEmphem package which includes built-in lists of
bright stars and solar system objects, ephemeris derived from two-line element (TLE) file for
Earth-orbiting artificial satellites, and raw RA/DEC coordinates.

An optional gamepad can be used to provide adjustments to the pointing in real time.
These adjustments are relative to the motion vector of the object across the sky.
"""

from __future__ import print_function
import sys
import ephem
import track

def main():

    parser = track.ArgParser()

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
        '--meridian-side',
        help='side of meridian for equatorial mounts to prefer',
        default='west'
    )
    parser.add_argument(
        '--lat',
        required=True,
        help='latitude of observer (+N)'
    )
    parser.add_argument(
        '--lon',
        required=True,
        help='longitude of observer (+E)'
    )
    parser.add_argument(
        '--elevation',
        required=True,
        help='elevation of observer (m)',
        type=float
    )
    parser.add_argument(
        '--loop-bw',
        help='control loop bandwidth (Hz)',
        default=0.5,
        type=float
    )
    parser.add_argument(
        '--loop-damping',
        help='control loop damping factor',
        default=2.0,
        type=float
    )
    parser.add_argument(
        '--telem-enable',
        help='enable logging of telemetry to database',
        action='store_true'
    )
    parser.add_argument(
        '--telem-db-host',
        help='hostname of InfluxDB database server',
        default='localhost'
    )
    parser.add_argument(
        '--telem-db-port',
        help='port number of InfluxDB database server',
        default=8086,
        type=int
    )
    parser.add_argument(
        '--telem-period',
        help='telemetry sampling period in seconds',
        default=1.0,
        type=float
    )

    subparsers = parser.add_subparsers(title='modes', dest='mode')

    parser_tle = subparsers.add_parser('tle', help='TLE file mode')
    parser_tle.add_argument('file', help='filename of two-line element (TLE) target ephemeris')

    parser_coord = subparsers.add_parser('coord', help='fixed-body coordinate mode')
    parser_coord.add_argument('ra', help='fixed body\'s right ascension', type=float)
    parser_coord.add_argument('dec', help='fixed body\'s declination', type=float)

    parser_star = subparsers.add_parser('star', help='named star mode')
    parser_star.add_argument('name', help='name of star')

    parser_star = subparsers.add_parser('solarsystem', help='named solar system body mode')
    parser_star.add_argument('name', help='name of planet or moon')

    args = parser.parse_args()

    # Create object with base type TelescopeMount
    if args.mount_type == 'nexstar':
        mount = track.NexStarMount(args.mount_path)
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
    elif args.mode == 'coord':
        print('In fixed-body coordinate mode: (RA {}, dec {}).'.format(args.ra, args.dec))
        target = ephem.FixedBody(_ra=args.ra, _dec=args.dec)

    # Get the PyEphem Body object corresonding to the given named star
    elif args.mode == 'star':
        print('In named star mode: \'{}\''.format(args.name))
        target = ephem.star(args.name)

    # Get the PyEphem Body object corresonding to the given named solar system body
    elif args.mode == 'solarsystem':
        print('In named solar system body mode: \'{}\''.format(args.name))
        ss_objs = [name for _, _, name in ephem._libastro.builtin_planets()]
        if args.name in ss_objs:
            body_type = getattr(ephem, args.name)
            target = body_type()
        else:
            raise Exception(
                'The solar system body \'{}\' isn\'t present in PyEphem.'.format(args.name)
            )
    else:
        print('No target specified!')
        sys.exit(1)

    # Create object with base type ErrorSource
    error_source = track.BlindErrorSource(
        mount,
        observer,
        target,
        meridian_side=args.meridian_side
    )
    telem_sources = {'error_blind': error_source}

    try:
        # Create gamepad object and register callback
        game_pad = track.Gamepad(
            left_gain=2.0,  # left stick degrees per second
            right_gain=0.5, # right stick degrees per second
            int_limit=5.0,  # max correction in degrees for either axis
        )
        game_pad.integrator_mode = True
        error_source.register_offset_callback(game_pad.get_integrator)
        telem_sources['gamepad'] = game_pad
        print('Gamepad found and registered.')
    except RuntimeError:
        print('No gamepads found.')

    tracker = track.Tracker(
        mount=mount,
        error_source=error_source,
        loop_bandwidth=args.loop_bw,
        damping_factor=args.loop_damping
    )
    telem_sources['tracker'] = tracker

    if args.telem_enable:
        telem_logger = track.TelemLogger(
            host=args.telem_db_host,
            port=args.telem_db_port,
            period=args.telem_period,
            sources=telem_sources,
        )
        telem_logger.start()

    try:
        tracker.run()
    except KeyboardInterrupt:
        print('Goodbye!')
    except Exception as e:
        print('Unhandled exception: ' + str(e))

    if args.telem_enable:
        telem_logger.stop()

if __name__ == "__main__":
    main()
