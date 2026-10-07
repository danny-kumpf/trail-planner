"""A recorded activity as samples in time, read from FIT or GPX files or Strava's streams.

Every source is read into the same `Recording`, so distance, vert, moving time and running come
out the same whichever format the activity was stored in.
"""

import gzip
import json
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import gpxpy
import numpy as np
from garmin_fit_sdk import Decoder, Stream

from trail_planner.trail_stats import great_circle_steps_m

# FIT timestamps count seconds from this moment.
FIT_EPOCH = datetime(1989, 12, 31, tzinfo=timezone.utc)
DEG_PER_SEMICIRCLE = 180 / 2**31
# Below this, positions are treated as not recorded, e.g. an indoor activity with a stray fix.
MIN_POSITION_COVERAGE_FRAC = 0.5
# Slower than this between two samples counts as stopped. On the account export this matched
# Strava's moving times to within 1% at the median for hikes, rides and watch-recorded runs.
# Phone-recorded runs on steep trails come out ~9% higher, as Strava counts slow climbing in a
# run as stopped (e.g. 9 of 66 min on the Manitou Incline).
DEFAULT_MIN_MOVING_SPEED_M_PER_S = 0.2


@dataclass(frozen=True)
class Recording:
    """Samples of one activity. A channel is None if it was never recorded; NaN marks a sample
    where it dropped out. Positions are either on every sample or None."""

    start: datetime
    # Wall-clock time where the activity was done, if the source says.
    local_start: datetime | None
    time_s: np.ndarray
    latitude_deg: np.ndarray | None = None
    longitude_deg: np.ndarray | None = None
    elevation_m: np.ndarray | None = None
    # The device's own count of distance so far, e.g. from a footpod on a treadmill.
    device_distance_m: np.ndarray | None = None
    cadence_strides_per_min: np.ndarray | None = None
    heart_rate_bpm: np.ndarray | None = None

    @property
    def num_samples(self) -> int:
        return len(self.time_s)

    @property
    def elapsed_s(self) -> float:
        return float(self.time_s[-1] - self.time_s[0])

    def step_m(self) -> np.ndarray:
        """Distance covered between consecutive samples: along the positions if recorded, else by
        the device's distance, else nothing."""
        if self.latitude_deg is not None:
            return great_circle_steps_m(self.latitude_deg, self.longitude_deg)
        if self.device_distance_m is not None:
            # Carry the last known distance over dropouts; a reset never counts as going backwards.
            known = np.maximum.accumulate(np.nan_to_num(self.device_distance_m, nan=0.0))
            return np.diff(known)
        return np.zeros(self.num_samples - 1)

    def along_track_m(self) -> np.ndarray:
        return np.concatenate([[0.0], np.cumsum(self.step_m())])

    def between(self, start: datetime, end: datetime) -> "Recording":
        """Only the samples from `start` to `end`, e.g. the part kept when an activity was cropped."""
        offset_s = (start - self.start).total_seconds()
        keep = (self.time_s >= offset_s) & (self.time_s <= (end - self.start).total_seconds())

        def kept(values: np.ndarray | None) -> np.ndarray | None:
            return None if values is None else values[keep]

        return checked(
            Recording(
                start=start,
                local_start=None if self.local_start is None else self.local_start + (start - self.start),
                time_s=self.time_s[keep] - offset_s,
                latitude_deg=kept(self.latitude_deg),
                longitude_deg=kept(self.longitude_deg),
                elevation_m=kept(self.elevation_m),
                device_distance_m=kept(self.device_distance_m),
                cadence_strides_per_min=kept(self.cadence_strides_per_min),
                heart_rate_bpm=kept(self.heart_rate_bpm),
            )
        )


def channel(values: list[float | None]) -> np.ndarray | None:
    """Per-sample values with NaN for missing ones, or None if no sample has one."""
    if all(v is None for v in values):
        return None
    return np.array([np.nan if v is None else float(v) for v in values])


def only_samples_with_position(recording: Recording) -> Recording:
    """Positions on every sample or not at all: drop the few samples without one (e.g. before GPS
    lock), or drop positions altogether if most samples lack them."""
    if recording.latitude_deg is None:
        return recording
    has_position = ~np.isnan(recording.latitude_deg) & ~np.isnan(recording.longitude_deg)
    if has_position.mean() < MIN_POSITION_COVERAGE_FRAC:
        return replace(recording, latitude_deg=None, longitude_deg=None)

    def keep(values: np.ndarray | None) -> np.ndarray | None:
        return None if values is None else values[has_position]

    return replace(
        recording,
        time_s=recording.time_s[has_position],
        latitude_deg=recording.latitude_deg[has_position],
        longitude_deg=recording.longitude_deg[has_position],
        elevation_m=keep(recording.elevation_m),
        device_distance_m=keep(recording.device_distance_m),
        cadence_strides_per_min=keep(recording.cadence_strides_per_min),
        heart_rate_bpm=keep(recording.heart_rate_bpm),
    )


def checked(recording: Recording) -> Recording:
    if recording.num_samples < 2:
        raise ValueError(f"need at least 2 samples, found {recording.num_samples}")
    return recording


def point_extension_value(point: gpxpy.gpx.GPXTrackPoint, tag: str) -> float | None:
    """The value of a TrackPointExtension field such as `cad` or `hr`, if the point has one."""
    for extension in point.extensions:
        for element in extension.iter():
            if element.tag.rsplit("}", 1)[-1] == tag:
                return float(element.text)
    return None


def recording_from_gpx(gpx: gpxpy.gpx.GPX) -> Recording:
    """Every point of every track, joined in order. GPX has no time zone, so no local start."""
    points = [p for track in gpx.tracks for segment in track.segments for p in segment.points]
    if not points:
        raise ValueError("no track points")
    if any(p.time is None for p in points):
        raise ValueError("some track points have no time")
    start = points[0].time
    return checked(
        Recording(
            start=start,
            local_start=None,
            time_s=np.array([(p.time - start).total_seconds() for p in points]),
            latitude_deg=np.array([p.latitude for p in points]),
            longitude_deg=np.array([p.longitude for p in points]),
            elevation_m=channel([p.elevation for p in points]),
            cadence_strides_per_min=channel([point_extension_value(p, "cad") for p in points]),
            heart_rate_bpm=channel([point_extension_value(p, "hr") for p in points]),
        )
    )


def fit_local_start(messages: dict, start: datetime) -> datetime | None:
    """The start as wall-clock time, from the offset between the activity's local and UTC stamps."""
    for activity in messages.get("activity_mesgs", []):
        local_timestamp = activity.get("local_timestamp")
        timestamp = activity.get("timestamp")
        if local_timestamp is not None and timestamp is not None:
            utc_offset = timedelta(seconds=local_timestamp) - (timestamp - FIT_EPOCH)
            return (start + utc_offset).replace(tzinfo=None)
    return None


def recording_from_fit(data: bytes) -> Recording:
    messages, errors = Decoder(Stream.from_byte_array(bytearray(data))).read()
    if errors:
        raise ValueError(f"unreadable FIT file: {errors[0]}")
    records = [r for r in messages.get("record_mesgs", []) if r.get("timestamp") is not None]
    if not records:
        raise ValueError("no records")
    start = records[0]["timestamp"]

    def field(name: str) -> list[float | None]:
        return [r.get(name) for r in records]

    def semicircles_to_deg(values: list[float | None]) -> np.ndarray | None:
        values_deg = channel(values)
        return None if values_deg is None else values_deg * DEG_PER_SEMICIRCLE

    cadence = [
        None if r.get("cadence") is None else r["cadence"] + (r.get("fractional_cadence") or 0.0)
        for r in records
    ]
    altitude_m = [r.get("enhanced_altitude", r.get("altitude")) for r in records]
    latitude_deg = semicircles_to_deg(field("position_lat"))
    longitude_deg = semicircles_to_deg(field("position_long"))
    has_positions = latitude_deg is not None and longitude_deg is not None
    return checked(
        only_samples_with_position(
            Recording(
                start=start,
                local_start=fit_local_start(messages, start),
                time_s=np.array([(r["timestamp"] - start).total_seconds() for r in records]),
                latitude_deg=latitude_deg if has_positions else None,
                longitude_deg=longitude_deg if has_positions else None,
                elevation_m=channel(altitude_m),
                device_distance_m=channel(field("distance")),
                cadence_strides_per_min=channel(cadence),
                heart_rate_bpm=channel(field("heart_rate")),
            )
        )
    )


def stream_values(streams: dict, key: str) -> list | None:
    """The values of one of Strava's streams, or None if it has no such stream."""
    stream = streams.get(key)
    return None if stream is None else stream["data"]


def stream_channel(streams: dict, key: str) -> np.ndarray | None:
    values = stream_values(streams, key)
    return None if values is None else channel(values)


def recording_from_streams(stored: dict) -> Recording:
    """Strava's streams for an activity, as a sync stores them: `{"start_utc": ..., "streams":
    <Strava's response, keyed by channel>}`. Strava gives the local start in the activity's
    summary, not here."""
    streams = stored["streams"]
    time_s = stream_values(streams, "time")
    if not time_s:
        raise ValueError("no time stream")
    latlng = stream_values(streams, "latlng")
    return checked(
        only_samples_with_position(
            Recording(
                start=datetime.fromisoformat(stored["start_utc"]),
                local_start=None,
                time_s=np.array(time_s, dtype=float),
                latitude_deg=None if latlng is None else channel([p[0] if p else None for p in latlng]),
                longitude_deg=None if latlng is None else channel([p[1] if p else None for p in latlng]),
                elevation_m=stream_channel(streams, "altitude"),
                device_distance_m=stream_channel(streams, "distance"),
                # Strava keeps the device's per-foot cadence, as FIT files do.
                cadence_strides_per_min=stream_channel(streams, "cadence"),
                heart_rate_bpm=stream_channel(streams, "heartrate"),
            )
        )
    )


def read_recording(path: Path) -> Recording:
    """A FIT or GPX file as Strava's export stores them, or Strava's streams as a sync stores them;
    any of them optionally gzipped."""
    data = path.read_bytes()
    suffixes = path.name.lower().split(".")[1:]
    if suffixes[-1:] == ["gz"]:
        data = gzip.decompress(data)
        suffixes = suffixes[:-1]
    match suffixes[-2:]:
        case [*_, "fit"]:
            return recording_from_fit(data)
        case [*_, "gpx"]:
            return only_samples_with_position(recording_from_gpx(gpxpy.parse(data.decode("utf-8"))))
        case ["streams", "json"]:
            return recording_from_streams(json.loads(data))
        case _:
            raise ValueError(f"unsupported file type: {path.name}")


def travels(recording: Recording) -> bool:
    """Whether the activity covers distance, as opposed to e.g. a gym workout."""
    return bool(recording.step_m().sum() > 0)


def moving_time_s(recording: Recording, min_moving_speed_m_per_s: float = DEFAULT_MIN_MOVING_SPEED_M_PER_S) -> float:
    """Time spent faster than `min_moving_speed_m_per_s` between samples; all of it for an
    activity that doesn't travel."""
    if not travels(recording):
        return recording.elapsed_s
    step_duration_s = np.diff(recording.time_s)
    is_moving = recording.step_m() > min_moving_speed_m_per_s * step_duration_s
    return float(step_duration_s[is_moving].sum())
