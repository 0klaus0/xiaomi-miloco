"""
RTSP camera device adapter — bridges user-managed RTSP cameras into perception.

Subscribes to BGR frame callbacks from RtspCameraService (OpenCV readers),
buffers them in a single-track MultiTrackSyncBuffer per device, and produces
DeviceData identical in shape to CameraDeviceAdapter's output so the
downstream pipeline (Omni engine, LLM) can consume RTSP frames seamlessly.

Unlike CameraDeviceAdapter which uses MiotProxy decoded streams (video + audio),
RTSP adapters only have video (no audio track from OpenCV).
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Callable

from miloco.config import get_settings
from miloco.perception.collect.adapter_base import BaseDeviceAdapter
from miloco.perception.collect.stream_buffer import (
    MultiTrackSyncBuffer,
    StreamFragment,
)
from miloco.perception.schema import (
    DecodedVideoFrame,
    DeviceData,
)
from miloco.perception.types import PerceptionDevice
from miloco.rtsp.service import get_rtsp_service

logger = logging.getLogger(__name__)

_RTSP_TRACKS = ["decoded_video"]


def _monotonic_ms() -> int:
    return time.monotonic_ns() // 1_000_000


def _unix_ms() -> int:
    return int(time.time() * 1000)


@dataclass
class _RtspDeviceState:
    """Per-RTSP-camera stream state."""

    did: str
    name: str
    url: str
    sync_buffer: MultiTrackSyncBuffer = field(
        default_factory=lambda: MultiTrackSyncBuffer(_RTSP_TRACKS)
    )
    epoch_delta: int | None = None


class RtspCameraAdapter(BaseDeviceAdapter):
    """RTSP camera adapter — OpenCV BGR frames via RtspCameraService."""

    device_type = "rtsp_camera"

    def __init__(
        self,
        on_window_ready: Callable[[], None] | None = None,
    ):
        self._on_window_ready = on_window_ready
        self._devices: dict[str, _RtspDeviceState] = {}

    async def discover_devices(
        self,
        all_devices: dict | None = None,
        online_only: bool = True,
        cap: bool = True,
    ) -> dict[str, PerceptionDevice]:
        """Discover RTSP cameras from the registry.

        Args:
            all_devices: Ignored — RTSP cameras have their own registry.
            online_only: If True, only return cameras with reachable RTSP streams.
            cap: Ignored — no MAX_ENABLED_CAMERAS limit for RTSP.
        """
        svc = get_rtsp_service()
        records = svc.list_records()
        result: dict[str, PerceptionDevice] = {}
        for record in records:
            if online_only and not svc.is_online(record.did):
                continue
            result[record.did] = PerceptionDevice(
                did=record.did,
                name=record.name,
                device_type="rtsp_camera",
                room_name=record.room_name or "RTSP",
                online=True,
            )
        return result

    async def connect_device(
        self, did: str, source: PerceptionDevice | None = None
    ) -> None:
        """Connect to an RTSP camera and subscribe to frame callbacks."""
        if did in self._devices:
            return

        svc = get_rtsp_service()
        record = svc.get(did)
        if record is None:
            logger.warning("RTSP camera %s not found in registry, cannot connect", did)
            return

        collect_cfg = get_settings().perception.collect

        state = _RtspDeviceState(
            did=did,
            name=record.name,
            url=record.url,
            sync_buffer=MultiTrackSyncBuffer(
                track_names=_RTSP_TRACKS,
                window_ms=collect_cfg.window_size * 1000,
                max_windows=collect_cfg.max_windows,
                on_window_ready=self._on_window_ready,
                window_settle_ms=collect_cfg.settle_ms,
                buffer_full_action=collect_cfg.full_action,
            ),
        )
        self._devices[did] = state

        # Register frame callback — feeds decoded_video track in sync buffer.
        # The RtspCameraService callback signature is:
        #   callback(did, frame_bgr, wall_ms, unix_ms, stream_ts)
        def _on_rtsp_frame(
            frame_did: str,
            frame,
            wall_ms: int,
            unix_ms: int,
            stream_ts: int,
        ):
            st = self._devices.get(frame_did)
            if st is None:
                return

            # Calibrate epoch_delta on first frame
            if st.epoch_delta is None:
                st.epoch_delta = _unix_ms() - _monotonic_ms()

            decoded = DecodedVideoFrame(
                frame=frame,
                stream_ts=stream_ts,
                wall_ms=wall_ms,
                unix_ms=unix_ms,
                recv_unix_ms=unix_ms,
                decoded_unix_ms=unix_ms,
                decode_latency_ms=0.0,
            )
            st.sync_buffer.put(
                "decoded_video", decoded, stream_ts=stream_ts, wall_ms=wall_ms
            )

        svc.add_frame_callback(did, f"perception_{did}", _on_rtsp_frame)
        logger.info("RTSP camera %s (%s) connected to perception", did, record.name)

    async def disconnect_device(self, did: str) -> None:
        """Disconnect from an RTSP camera, remove callback, clear buffer."""
        state = self._devices.pop(did, None)
        if not state:
            return

        svc = get_rtsp_service()
        svc.remove_frame_callback(did, f"perception_{did}")
        state.sync_buffer.clear()
        logger.info("RTSP camera %s disconnected from perception", did)

    def collect(self, did: str, *, drain: bool = True) -> DeviceData | None:
        """Collect multimodal data from the RTSP camera's sync buffer."""
        state = self._devices.get(did)
        if not state:
            return None

        if drain:
            ready = state.sync_buffer.drain_ready()
            if ready is None or not any(ready.tracks.values()):
                return None
            dropped, ovf_cnt, max_depth, last_action = (
                state.sync_buffer.consume_drop_stats()
            )
            return self._build_device_data(
                state,
                ready.tracks,
                window_start_ms=ready.start_ms,
                window_end_ms=ready.end_ms,
                dropped_windows=dropped,
                overflow_count=ovf_cnt,
                max_buffer_depth=max_depth,
                last_overflow_action=last_action,
            )
        else:
            collect_ms = get_settings().perception.collect.window_size * 1000
            tracks = state.sync_buffer.peek_latest(duration_ms=collect_ms)
            if tracks is None or not any(tracks.values()):
                return None
            return self._build_device_data(state, tracks)

    def get_connected_devices(self) -> dict[str, PerceptionDevice]:
        return {
            did: PerceptionDevice(
                did=did,
                name=state.name,
                device_type="rtsp_camera",
                room_name=state.name,
                online=True,
            )
            for did, state in self._devices.items()
        }

    def clear_buffers(self) -> None:
        """Clear all RTSP camera sync buffers without disconnecting."""
        for did, state in self._devices.items():
            state.sync_buffer.clear()
            logger.info("Cleared sync buffer for RTSP camera %s", did)

    def peek_latest_frame(self, did: str, *, window_ms: int = 2000):
        """Non-destructive peek at the latest BGR frame for live detection."""
        state = self._devices.get(did)
        if state is None:
            return None
        tracks = state.sync_buffer.peek_latest(duration_ms=window_ms)
        if not tracks:
            return None
        dv_frags = tracks.get("decoded_video", [])
        if not dv_frags:
            return None
        return getattr(dv_frags[-1].data, "frame", None)

    @staticmethod
    def _wall_to_unix(state: _RtspDeviceState, wall_ms: int) -> int:
        if state.epoch_delta is not None:
            return wall_ms + state.epoch_delta
        return 0

    def _build_device_data(
        self,
        state: _RtspDeviceState,
        tracks: dict[str, list[StreamFragment]],
        window_start_ms: int = 0,
        window_end_ms: int = 0,
        *,
        dropped_windows: int = 0,
        overflow_count: int = 0,
        max_buffer_depth: int = 0,
        last_overflow_action: str | None = None,
    ) -> DeviceData | None:
        """Build DeviceData from RTSP frame track fragments."""
        dv_frags = tracks.get("decoded_video", [])

        if not dv_frags:
            return None

        video = [f.data for f in dv_frags]
        v_count = len(video)

        def _avg(sum_: float, count: int) -> float:
            return (sum_ / count) if count else 0.0

        v_decode_sum = sum(f.decode_latency_ms for f in video)
        decode_video_avg = _avg(v_decode_sum, v_count)

        return DeviceData(
            meta=PerceptionDevice(
                did=state.did,
                name=state.name,
                device_type="rtsp_camera",
                room_name=state.name,
                online=True,
            ),
            video=video,
            audio=[],  # RTSP has no audio track
            window_start_ms=window_start_ms,
            window_end_ms=window_end_ms,
            window_start_unix_ms=self._wall_to_unix(state, window_start_ms),
            window_end_unix_ms=self._wall_to_unix(state, window_end_ms),
            decode_avg_ms=decode_video_avg,
            decode_video_avg_ms=decode_video_avg,
            decode_audio_avg_ms=0.0,
            dropped_windows=dropped_windows,
            overflow_count=overflow_count,
            max_buffer_depth=max_buffer_depth,
            last_overflow_action=last_overflow_action,
        )
