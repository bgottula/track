"""tracking loop for telescope control.

Track provides the classes required to point a telescope with software using a
feedback control loop.

"""

from __future__ import print_function
import time
import abc
from track.telem import TelemSource
from track.mathutils import clamp


class ErrorSource(object):
    """Abstract parent class for error sources.

    This class provides some abstract methods to provide a common interface
    for error sources to be used in the tracking loop. All error sources must
    inheret from this class and implement the methods defined.
    """
    __metaclass__ = abc.ABCMeta

    class NoSignalException(Exception):
        """Raised when no signal is available for error calculation."""
        pass

    @abc.abstractmethod
    def get_axis_names(self):
        """Get axis names

        Returns:
            A list of strings giving abbreviated names of each axis. These must
            be the keys of the dict returned by the compute_error method.
        """
        pass

    @abc.abstractmethod
    def compute_error(self, retries=0):
        """Computes the error signal.

        Args:
            retries: Some error sources rely on unreliable detectors such as
                computer vision which may occasionally fail to work on the
                first try. A positive value allows multiple tries before
                giving up.

        Returns:
            The pointing error as a dict with entries for each axis. The units
            should be degrees.

        Raises:
            NoSignalException: If the error cannot be computed.
        """
        pass


class TelescopeMount(object):
    """Abstract parent class for telescope mounts.

    This class provides some abstract methods to provide a common interface
    for telescope mounts to be used in the tracking loop. All mounts must
    inheret from this class and implement the methods defined. Only Az-Alt
    mounts are currently supported.
    """
    __metaclass__ = abc.ABCMeta

    class AxisLimitException(Exception):
        """Raised when an axis reaches or exceeds limits."""
        def __init__(self, axes):
            self.axes = axes

    @abc.abstractmethod
    def get_axis_names(self):
        """Get axis names

        Returns:
            A list of strings giving abbreviated names of each axis. These must
            be the keys of the dicts used by the get_position,
            get_aligned_slew_dir, remove_backlash, and get_max_slew_rates
            methods and the names accepted for the axis argument of the slew
            method.
        """

    @abc.abstractmethod
    def get_position(self, max_cache_age=0.0):
        """Gets the current position of the mount.

        Args:
            max_cache_age: If the position has been read from the mount less
                than this many seconds ago, the function may return a cached
                position value in lieu of reading the position from the mount.
                In cases where reading from the mount is relatively slow this
                may allow the function to return much more quickly. The default
                value is set to 0 seconds, in which case the function will
                never return a cached value.

        Returns:
            A dict with keys for each axis where the values are the positions
            in degrees.
        """
        pass

    @abc.abstractmethod
    def slew(self, axis, rate):
        """Command the mount to slew on one axis.

        Commands the mount to slew at a paritcular rate in one axis.

        Args:
            axis: A string indicating the axis.
            rate: A float giving the slew rate in degrees per second. The sign
                of the value indicates the direction of the slew.

        Raises:
            AltitudeLimitException: Implementation dependent.
        """
        pass

    @abc.abstractmethod
    def get_max_slew_rates(self):
        """Get the max supported slew rates.

        Returns:
            A dict with keys for each axis where the values are the maximum
            allowed slew rates in degrees per second.
        """
        pass

    @abc.abstractmethod
    def get_max_slew_accels(self):
        """Get the max supported slew accelerations.

        Returns:
            A dict with keys for each axis where the values are the maximum
            allowed slew accelerations in degrees per second squared.
        """
        pass

    @abc.abstractmethod
    def get_max_slew_steps(self):
        """Get the max supported slew steps.

        Returns:
            A dict with keys for each axis where the values are the maximum allowed slew steps in
            degrees per second. The slew step is defined as the maximum magnitude change in slew
            rate between consecutive slew commands.
        """
        pass


class LoopFilter(object):
    """Proportional plus integral (PI) loop filter.

    This class implements a standard proportional plus integral loop filter.
    The proportional and integral coefficients are computed on the fly in order
    to support a dynamic loop period.

    Attributes:
        bandwidth: Loop bandwidth in Hz.
        damping_factor: Loop damping factor.
        max_update_period: Maximum tolerated loop update period in seconds.
        rate_limit: Maximum allowed slew rate in degrees per second.
        accel_limit: Maximum allowed slew acceleration in degrees per second
            squared.
        step_limit: Maximum allowed change in slew rate per update in degrees per second.
        int: Integrator value.
        last_iteration_time: Unix time of last call to update().
        last_rate: Output value returned on last call to update().
    """
    def __init__(
            self,
            bandwidth,
            damping_factor,
            rate_limit=None,
            accel_limit=None,
            step_limit=None,
            max_update_period=0.1
        ):
        """Inits a Loop Filter object.

        Args:
            bandwidth: Loop bandwidth in Hz.
            damping_factor: Loop damping factor.
            rate_limit: Slew rate limit in degrees per second. Set to None to remove limit.
            accel_limit: Slew acceleration limit in degrees per second squared. Set to None to
                remove limit.
            step_limit: Maximum change in slew rate allowed per update in degrees per second. Set
                to None to remove limit.
            max_update_period: Maximum tolerated loop update period in seconds.
        """
        self.bandwidth = bandwidth
        self.damping_factor = damping_factor
        self.max_update_period = max_update_period
        self.rate_limit = rate_limit
        self.accel_limit = accel_limit
        self.step_limit = step_limit
        self.int = 0.0
        self.last_iteration_time = None
        self.last_rate = 0.0

    def update(self, error):
        """Update the loop filter using new error signal input.

        Updates the loop filter using new error signal information. The loop
        filter proportional and integral coefficients are calculated on each
        call based on the time elapsed since the previous call. This allows
        the loop response to remain consistent even if the loop period is
        changing dynamically or can't be predicted in advance.

        If this method was last called more than max_update_period seconds ago
        a warning will be printed and the stored integrator value will be
        returned. The error signal will be ignored. This is meant to protect
        against edge cases where long periods between calls to update() could
        cause huge disturbances to the loop behavior.

        The integrator and output of the loop filter will be limited to not
        exceed the maximum slew rate as defined by the rate_limit constructor
        argument. The integrator and output will also be limited to prevent
        the slew acceleration from exceeding the accel_limit constructor
        argument, if specified.

        Args:
            error: The error in phase units (typically degrees).

        Returns:
            The slew rate to be applied in the same units as the error signal
                (typically degrees).
        """

        # can't measure loop period on first update
        if self.last_iteration_time is None:
            self.last_iteration_time = time.time()
            return 0.0

        update_period = time.time() - self.last_iteration_time
        self.last_iteration_time = time.time()
        if update_period > self.max_update_period:
            print('Warning: loop filter update period was '
                  + str(update_period) + ' s, limit is '
                  + str(self.max_update_period) + ' s.')
            return self.int

        # compute loop filter gains based on loop period
        bt = self.bandwidth * update_period
        k0 = update_period
        denom = self.damping_factor + 1.0 / (4.0 * self.damping_factor)
        prop_gain = 4.0 * self.damping_factor / denom * bt / k0
        int_gain = 4.0 / denom**2.0 * bt**2.0 / k0

        # proportional term
        prop = prop_gain * -error

        # integral term
        if self.rate_limit is not None:
            self.int = clamp(self.int + int_gain * -error, self.rate_limit)
        else:
            self.int = self.int + int_gain * -error

        # new slew rate is the sum of P and I terms subject to rate limit
        if self.rate_limit is not None:
            rate = clamp(prop + self.int, self.rate_limit)
        else:
            rate = prop + self.int

        # enforce slew acceleration limit
        if self.accel_limit is not None:
            rate_change = rate - self.last_rate
            if abs(rate_change) / update_period > self.accel_limit:
                rate_change = clamp(rate_change, self.accel_limit * update_period)
                rate = self.last_rate + rate_change
                self.int = clamp(self.int, abs(rate))

        # enforce a max slew rate step size
        if self.step_limit is not None:
            if abs(rate_change) > self.step_limit:
                rate_change = clamp(rate_change, self.step_limit)
                rate = self.last_rate + rate_change
                self.int = clamp(self.int, abs(rate))

        self.last_rate = rate

        return rate


class Tracker(TelemSource):
    """Main tracking loop class.

    This class is the core of the track package. A tracking loop or control
    loop is a system that uses feedback for control. In this case, the thing
    under control is a telescope mount. Slew commands are sent to the mount
    at regular intervals to keep it pointed in the direction of some object.
    In order to know which direction the mount should slew, the tracking loop
    needs feedback which comes in the form of an error signal. The error signal
    is a measure of the difference between where the telescope is pointed now
    compared to where the object is. The control loop tries to drive the error
    signal magnitude to zero. The final component in the loop is a loop filter
    ("loop controller" might be a better name, but "loop filter" is the name
    everyone uses). The loop filter controls the response characteristics of
    the system including the bandwidth (responsiveness to changes in input)
    and the damping factor.

    Attributes:
        loop_filter: A dict with keys for each axis where the values are
            LoopFilter objects. Each axis has its own independent loop filter.
        mount: An object of type TelescopeMount. This represents the interface
            to the mount.
        error_source: An object of type ErrorSource. The error can be computed
            in many ways so an abstract class with a generic interface is used.
        error: Cached error value returned by the error_source object's
            compute_error() method. This is cached so that callback methods
            can make use of it if needed. A dict with keys for each axis.
        slew_rate: Cached slew rates from the most recent loop filter output.
            Cached to make it available to callbacks. A dict with keys for each
            axis.
        num_iterations: A running count of the number of iterations.
        callback: A callback function. The callback will be called once at the
            end of every control loop iteration. Set to None if no callback is
            registered.
        stop: Boolean value. The control loop checks this on every iteration
            and stops if the value is True.
    """

    def __init__(self, mount, error_source, loop_bandwidth, damping_factor):
        """Inits a Tracker object.

        Initializes a Tracker object by constructing loop filters and
        initializing state information.

        Args:
            mount: Object of type TelescopeMount. Must use the same set of axes
                as error_source.
            error_source: Object of type ErrorSource. Must use the same set of
                axes as mount.
            loop_bandwidth: The loop bandwidth in Hz.
            damping_factor: The damping factor. Keep in mind that the motors in
                the mount will not respond instantaneously to slew commands,
                therefore the damping factor may need to be higher than an
                ideal system would suggest. Common values like sqrt(2)/2 may be
                too small to prevent oscillations.
        """
        if set(mount.get_axis_names()) != set(error_source.get_axis_names()):
            raise ValueError('error_source and mount must use same set of axes')
        self.loop_filter = {}
        self.error = {}
        self.slew_rate = {}
        for axis in mount.get_axis_names():
            self.loop_filter[axis] = LoopFilter(
                bandwidth=loop_bandwidth,
                damping_factor=damping_factor,
                rate_limit=mount.get_max_slew_rates()[axis],
                accel_limit=mount.get_max_slew_accels()[axis],
                step_limit=mount.get_max_slew_steps()[axis],
            )
            self.error[axis] = None
            self.slew_rate[axis] = 0.0
        self.mount = mount
        self.error_source = error_source
        self.num_iterations = 0
        self.callback = None
        self.stop = False

    def register_callback(self, callback):
        """Register a callback function.

        Registers a callback function to be called near the end of each loop
        iteration. The callback function is called with no arguments.

        Args:
            callback: The function to call. None to un-register.
        """
        self.callback = callback

    def run(self, axes=None):
        """Run the control loop.

        Call this method to start the control loop. This function is blocking
        and will not return until an error occurs or until the stop attribute
        has been set to True. Immediately after invocation this function will
        set the stop attribute to False.

        Args:
            axes: A list of strings indicating which axes should be under
                active tracking control. The slew rate on any axis not included
                in the list will not be commanded by the control loop. If an
                empty list is passed the function returns immediately. If None
                is passed all axes will be active.
        """
        self.stop = False

        if axes is None:
            axes = self.mount.get_axis_names()

        if len(axes) == 0:
            return

        while True:

            if self.stop:
                return

            # compute error
            try:
                self.error = self.error_source.compute_error()
            except ErrorSource.NoSignalException:
                for axis in self.mount.get_axis_names():
                    self.error[axis] = None
                self.finish_control_cycle()
                break

            # update loop filters
            for axis in axes:
                self.slew_rate[axis] = self.loop_filter[axis].update(self.error[axis])

            # set mount slew rates
            for axis in axes:
                try:
                    self.mount.slew(axis, self.slew_rate[axis])
                except TelescopeMount.AxisLimitException:
                    self.loop_filter[axis].int = 0.0

            self.finish_control_cycle()

    def finish_control_cycle(self):
        """Final tasks to perform at the end of each control cycle."""
        if self.callback is not None:
            self.callback()
        self.num_iterations += 1

    def get_telem_channels(self):
        chans = {}
        chans['num_iterations'] = self.num_iterations
        for axis in self.mount.get_axis_names():
            chans['rate_' + axis] = self.slew_rate[axis]
            chans['error_' + axis] = self.error[axis]
            chans['loop_filt_int_' + axis] = self.loop_filter[axis].int
        return chans
