#!/usr/bin/env python

import argparse
import mounts
import time
import errorsources

parser = argparse.ArgumentParser()
parser.add_argument('--scope', help='serial device for connection to telescope', default='/dev/ttyUSB0')
args = parser.parse_args()

# Create object with base type TelescopeMount
mount = mounts.NexStarMount(args.scope)

position_start = mount.get_azalt()

while True:
    #time.sleep(0.1)
    position = mount.get_azalt()
    position_change = {
        'az': errorsources.wrap_error(position['az'] - position_start['az']) * 3600.0,
        'alt': errorsources.wrap_error(position['alt'] - position_start['alt']) * 3600.0,
    }
    print(str(position_change))
