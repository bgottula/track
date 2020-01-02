"""Mount modeling and transformations between sky and mount reference frames"""

import os
from typing import NamedTuple
import numpy as np
import pandas as pd
import scipy.optimize
import matplotlib.pyplot as plt
from astropy.utils import iers
from astropy import units as u
from astropy.time import Time
from astropy.coordinates import SkyCoord, Longitude, Angle, EarthLocation
from astropy.coordinates.matrix_utilities import rotation_matrix
from astropy.coordinates.representation import UnitSphericalRepresentation, CartesianRepresentation
from track.config import CONFIG_PATH
from track.mounts import MeridianSide, MountEncoderPositions


DEFAULT_MODEL_FILENAME = os.path.join(CONFIG_PATH, 'model_params.pickle')


# Try to download IERS data if online but disable strict checking in subsequent calls to methods
# that depend on this data if it is a bit stale. Ideally this data should be fresh, so if this code
# is to be used offline it would be best to provide a mechanism for updating Astropy's IERS cache
# whenever network connectivity is available.
# See https://docs.astropy.org/en/stable/utils/iers.html for additional details.
iers.IERS_Auto().open()  # Will try to download if cache is stale
iers.conf.auto_max_age = None  # Disable strict stale cache check


class ModelParameters(NamedTuple):
    """Set of parameters for the mount model.

    When paired with the equations in the world_to_mount and mount_to_world functions these
    parameters define a unique transformation between mount encoder positions and coordinates in
    a local equatorial coordinate system (hour angle and declination). When further augmented with
    a location and time, these local coordinates can be further transformed to positions on the
    celestial sphere (right ascension and declination).

    Attributes:
        axis_0_offset: Encoder zero-point offset for the longitude mount axis. For equatorial
            mounts this is the right ascension axis. For altazimuth mounts this is the azimuth
            axis.
        axis_1_offset: Encoder zero-point offset for the latitude mount axis. For equatorial
            mounts this is the declination axis. For altazimuth mounts this is the altitude axis.
        pole_rot_axis_lon: The longitude angle of the axis of rotation used to transform from a
            spherical coordinate system using the mount physical pole to a coordinate system using
            the celestial pole.
        pole_rot_angle: The angular separation between the instrument pole and the celestial pole.
    """
    axis_0_offset: Angle
    axis_1_offset: Angle
    pole_rot_axis_lon: Angle
    pole_rot_angle: Angle

    @staticmethod
    def from_ndarray(param_array):
        """Factory method to generate an instance of this class with values given in an ndarray.

        Args:
            param_array (ndarray): An array of parameter values. This format is required when
                interfacing with the scipy least_squares method.
        """
        return ModelParameters(
            axis_0_offset=Angle(param_array[0]*u.deg),
            axis_1_offset=Angle(param_array[1]*u.deg),
            pole_rot_axis_lon=Angle(param_array[2]*u.deg),
            pole_rot_angle=Angle(param_array[3]*u.deg),
        )

    def to_ndarray(self):
        """Return an ndarray containing the model parameters.

        The output format is suitable for use with scipy least_squares.

        Returns:
            An ndarray object containing the parameter values.
        """
        return np.array([
            self.axis_0_offset.deg,
            self.axis_1_offset.deg,
            self.pole_rot_axis_lon.deg,
            self.pole_rot_angle.deg,
        ])


class ModelParamSet(NamedTuple):
    """Collection of mount model parameters and the location and time where they were generated.

    The purpose of this class is to pair an instance of ModelParameters with the location where
    they were generated and an approximate time of generation. An instance of this object can be
    pickled and stored on disk for later usage. The location is important because the model
    parameters are only valid for that location. The timestamp is included to allow for checks on
    the freshness of the parameters--for example, if they are more than a few hours old they may
    no longer be trustworthy unless the mount is a permanent installation.

    Attributes:
        model_params: Instance of ModelParameters as defined in this module.
        location: Location of the mount corresponding to model_params.
        timestamp: Unix timestamp giving the approximate time that this set of model parameters
            was generated.
    """
    model_params: ModelParameters
    location: EarthLocation
    timestamp: float


def tip_axis(coord, axis_lon, rot_angle):
    """Perform a rotation about an axis perpendicular to the Z-axis.

    The purpose of this rotation is to move the pole of the coordinate system from one place to
    another. For example, transforming from a coordinate system where the pole is aligned with the
    physical pole of a mount to a celestial coordinate system where the pole is aligned with the
    celestial pole.

    Note that this is a true rotation, and the same rotation is applied to all coordinates in the
    originating coordinate system equally. It is not equivalent to using SkyCoord
    directional_offset_by() with a fixed position angle and separation since the direction and
    magntidue of the offset depend on the value of coord.

    Args:
        coord (UnitSphericalRepresentation): Coordinate to be transformed.
        axis_lon (Angle): Longitude angle of the axis of rotation.
        rot_angle (Angle): Angle of rotation.

    Returns:
        UnitSphericalRepresentation of the coordinate after rotation is applied.
    """
    rot = rotation_matrix(rot_angle, axis=SkyCoord(axis_lon, 0*u.deg).represent_as('cartesian').xyz)
    coord_cart = coord.represent_as(CartesianRepresentation)
    coord_rot_cart = coord_cart.transform(rot)
    return coord_rot_cart.represent_as(UnitSphericalRepresentation)


class MountModel:
    """A math model of a telescope mount.

    This class provides transformations between mount encoder position readings and coordinates in
    the celestial equatorial reference frame.

    TODO: This class really ought to store the location, since the ModelParameters are only valid
    for a particular location and this relieves the callers of these methods from needing to
    provide instances of the Time class that are already populated with a matching location which
    could allow subtle errors. Rather than requiring Time objects to be initialized with a location,
    sidereal_time() should be called with the optional longitude argument set.

    Attributes:
        model_params (ModelParameters): The set of parameters to be used in the transformations.
    """


    def __init__(self, model_params):
        """Construct an instance of MountModel"""
        self.model_params = model_params


    def mount_to_world(
            self,
            encoder_positions,
            t,
        ):
        """Convert coordinate in mount frame to coordinate in celestial equatorial frame.

        Args:
            encoder_positions (MountEncoderPositions): Set of mount encoder positions.
            t (Time): An Astropy Time object. This must be initialized with a location as well as
                time.

        Returns:
            A SkyCoord object with the right ascension and declination coordinates in the celestial
                coordinate system.
        """

        # apply encoder offsets
        encoders_corrected = MountEncoderPositions(
            Longitude(encoder_positions[0] - self.model_params.axis_0_offset),
            Longitude(encoder_positions[1] - self.model_params.axis_1_offset),
        )

        us_mnt_offset = encoder_to_spherical(encoders_corrected)

        # transform from mount pole to celestial pole
        us_local = tip_axis(
            us_mnt_offset,
            self.model_params.pole_rot_axis_lon,
            -self.model_params.pole_rot_angle
        )

        # Calculate the right ascension at this time and place corresponding to the hour angle.
        ra = t.sidereal_time('mean') - us_local.lon

        return SkyCoord(ra, us_local.lat, frame='icrs')


    def world_to_mount(
            self,
            sky_coord,
            meridian_side,
            t,
        ):
        """Convert coordinate in celestial equatorial frame to coordinate in mount frame.

        Args:
            sky_coord (SkyCoord): Celestial coordinate to be converted.
            meridian_side (MeridianSide): Gives the desired side of the meridian to use (for
                equatorial mounts).
            t (Time): An Astropy Time object. This must be initialized with a location as well as
                time.

        Returns:
            MountEncoderPositions object with the encoder positions corresponding to this sky
                coordinate and on the desired side of the meridian.
        """

        # Calculate hour angle corresponding to SkyCoord right ascension at this time and place
        ha = t.sidereal_time('mean') - sky_coord.ra
        sc_local = SkyCoord(ha, sky_coord.dec)

        # transform from celestial pole to mount pole
        us_mnt_offset = tip_axis(
            sc_local,
            self.model_params.pole_rot_axis_lon,
            self.model_params.pole_rot_angle
        )

        encoders_uncorrected = spherical_to_encoder(us_mnt_offset, meridian_side)

        # apply encoder offsets
        encoder_positions = MountEncoderPositions(
            Longitude(encoders_uncorrected[0] + self.model_params.axis_0_offset),
            Longitude(encoders_uncorrected[1] + self.model_params.axis_1_offset),
        )

        return encoder_positions


def spherical_to_encoder(mount_coord, meridian_side=MeridianSide.EAST):
    """Convert from mount-relative spherical coordinates to mount encoder positions

    The details of the transformation applied here follow the conventions used by the Losmandy G11
    mount's "physical" encoder position "pra" and "pdec". In particular, the default starting
    values of the encoders in the "counterweight down" startup position are used. This should still
    work with other mounts as long as the "handedness" of the encoders is the same. The encoder
    zero point offsets in the mount model should take care of any difference in the startup
    positions.

    Args:
        mount_coord (UnitSphericalRepresentation): Coordinate in the mount frame.
        meridian_side (MeridianSide): Desired side of mount-relative meridian. If the pole of the
            mount is not in the direction of the celestial pole this may not correspond to true
            east and west directions.

    Returns:
        An instance of MountEncoderPositions.
    """

    # TODO: This transformation is only correct if the mount axes are exactly orthogonal. This
    # should be replaced with a more general transformation that can handle non-orthogonal axes.
    if meridian_side == MeridianSide.EAST:
        encoder_0 = Longitude(90*u.deg - mount_coord.lon)
        encoder_1 = Longitude(90*u.deg + mount_coord.lat)
    else:
        encoder_0 = Longitude(270*u.deg - mount_coord.lon)
        encoder_1 = Longitude(270*u.deg - mount_coord.lat)
    return MountEncoderPositions(encoder_0, encoder_1)


def encoder_to_spherical(encoder_positions):
    """Convert from mount encoder positions to mount-relative spherical coordinates.

    The details of the transformation applied here follow the conventions used by the Losmandy G11
    mount's "physical" encoder position "pra" and "pdec". In particular, the default starting
    values of the encoders in the "counterweight down" startup position are used. This should still
    work with other mounts as long as the "handedness" of the encoders is the same. The encoder
    zero point offsets in the mount model should take care of any difference in the startup
    positions.

    Args:
        encoder_positions (MountEncoderPositions): Set of mount encoder positions to be converted.

    Returns:
        UnitSphericalRepresentation where longitude angle is like hour angle and the latitude
            angle is like declination but in the mount reference frame. These may not correspond
            to true hour angle and declination depending on how the polar axis of the mount is
            oriented.
    """

    # TODO: This transformation is only correct if the mount axes are exactly orthogonal. This
    # should be replaced with a more general transformation that can handle non-orthogonal axes.
    if encoder_positions[1] < 180*u.deg:  # east of mount meridian
        mount_lon = 90*u.deg - encoder_positions[0]
        mount_lat = encoder_positions[1] - 90*u.deg
    else:  # west of mount meridian
        mount_lon = 270*u.deg - encoder_positions[0]
        mount_lat = 270*u.deg - encoder_positions[1]

    return UnitSphericalRepresentation(mount_lon, mount_lat)


def residual(observation, model_params, location):
    """Compute the residual (error) between observed and modeled positions

    Args:
        observation: A Pandas Series containing a single observation.
        model_params (ModelParameters): Set of mount model parameters.
        location: An EarthLocation object.

    Returns:
        A Pandas Series containing a separation angle and position angle.
    """
    encoder_positions = MountEncoderPositions(
        Longitude(observation.encoder_0*u.deg),
        Longitude(observation.encoder_1*u.deg),
    )
    mount_model = MountModel(model_params)
    sc_mount = mount_model.mount_to_world(
        encoder_positions,
        Time(observation.unix_timestamp, format='unix', location=location),
    )
    sc_cam = SkyCoord(observation.solution_ra*u.deg, observation.solution_dec*u.deg, frame='icrs')

    return pd.Series([sc_mount.separation(sc_cam), sc_mount.position_angle(sc_cam)],
                     index=['separation', 'position_angle'])


def residuals(param_array, observations, location):
    """Generate series of residuals for a set of observations and model parameters.

    This is intended for use as the callback function passed to scipy.optimize.least_squares.

    Args:
        param_array (ndarray): Set of model parameters.
        observations (dataframe): Data from observations.
        location (EarthLocation): Observer location.

    Returns:
        A Pandas Series containing the magnitudes of the residuals in degrees.
    """
    res = observations.apply(
        residual,
        axis='columns',
        reduce=False,
        args=(ModelParameters.from_ndarray(param_array), location)
    ).separation
    return res.apply(lambda res_angle: res_angle.deg)


def plot_residuals(model_params, observations, location):
    """Plot the residuals on a polar plot.

    Args:
        model_params (ModelParameters): Set of model parameters.
        observations (dataframe): Data from observations.
        location (EarthLocation): Observer location.
    """
    res = observations.apply(
        residual,
        axis='columns',
        reduce=False,
        args=(model_params, location)
    )
    position_angles = res.position_angle.apply(lambda x: x.rad)
    separations = res.separation.apply(lambda x: x.arcmin)
    plt.polar(position_angles, separations, 'k.', label='residuals')
    plt.polar(np.linspace(0, 2*np.pi, 100), 90*np.ones(100), 'r', label='camera FOV')
    plt.title('Model Residuals (magnitude in arcminutes)')
    plt.legend()


class NoSolutionException(Exception):
    """Raised when optimization algorithm to solve for mount model parameters fails."""


def solve_model(observations, location):
    """Solves for mount model parameters using a set of observations and location.

    Finds a least-squares solution to the mount model parameters. The solution can then be used
    with the world_to_mount and mount_to_world functions in this module to convert between mount
    reference frame and celestial equatorial frame.

    Args:
        observations (dataframe): A set of observations where each contains a timestamp, mount
            encoder positions, and the corresponding celestial coordinates.
        location (EarthLocation): Location from which the observations were made.

    Returns:
        A ModelParameters object containing the solution.

    Raises:
        NoSolutionException if a solution could not be found.
    """

    # best starting guess for parameters
    init_values = ModelParameters(
        axis_0_offset=Angle(0*u.deg),
        axis_1_offset=Angle(0*u.deg),
        pole_rot_axis_lon=Angle(0*u.deg),
        pole_rot_angle=Angle(0*u.deg),
    )

    # lower bound on allowable values for each model parameter
    min_values = ModelParameters(
        axis_0_offset=Angle(-180*u.deg),
        axis_1_offset=Angle(-180*u.deg),
        pole_rot_axis_lon=Angle(-180*u.deg),
        pole_rot_angle=Angle(-180*u.deg),
    )

    # upper bound on allowable values for each model parameter
    max_values = ModelParameters(
        axis_0_offset=Angle(180*u.deg),
        axis_1_offset=Angle(180*u.deg),
        pole_rot_axis_lon=Angle(180*u.deg),
        pole_rot_angle=Angle(180*u.deg),
    )

    result = scipy.optimize.least_squares(
        residuals,
        init_values.to_ndarray(),
        bounds=(
            min_values.to_ndarray(),
            max_values.to_ndarray(),
        ),
        args=(observations, location),
    )

    if not result.success:
        raise NoSolutionException(result.message)

    return ModelParameters.from_ndarray(result.x), result
