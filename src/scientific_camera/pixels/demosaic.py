"""Explicit linear RGGB interpolation and a separate best-effort preview worker."""

from __future__ import annotations

import threading

from ..errors import CameraError, CaptureTimeoutError, ConfigurationError, UnsupportedFeatureError


def demosaic_rggb(mosaic):
    """Bilinear RGB in original sample units; no gamma/WB/black-level/colour matrix."""
    import numpy as np

    height, width = mosaic.shape
    if height < 2 or width < 2:
        raise ConfigurationError("RGGB demosaic requires at least 2x2 samples")
    result = np.empty((height, width, 3), dtype=np.uint16)
    for channel, positions in enumerate((((0, 0),), ((0, 1), (1, 0)), ((1, 1),))):
        mask = np.zeros((height, width), dtype=np.float64)
        for y, x in positions:
            mask[y::2, x::2] = 1
        samples = np.pad(mosaic.astype(np.float64) * mask, 1, mode="symmetric")
        padded_mask = np.pad(mask, 1, mode="symmetric")
        total = np.zeros((height, width), dtype=np.float64)
        weight = np.zeros_like(total)
        for y, wy in enumerate((1, 2, 1)):
            for x, wx in enumerate((1, 2, 1)):
                total += wy * wx * samples[y : y + height, x : x + width]
                weight += wy * wx * padded_mask[y : y + height, x : x + width]
        values = np.rint(total / np.maximum(weight, 1))
        result[:, :, channel] = np.where(mask, mosaic, values).astype(np.uint16)
    return result


class PreviewWorker:
    """Own worker; last_image is best effort. Never runs conversions on acquisition thread."""

    def __init__(self, stream, max_dimension=256):
        if max_dimension < 2:
            raise ConfigurationError("preview dimension must be at least 2")
        self.stream, self.max_dimension = stream, max_dimension
        self.last_image = None
        self.error = None
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="scicam-preview", daemon=True)
        self._thread.start()

    def _run(self):
        from .registry import lookup
        from .standard import decode

        previous = -1
        try:
            while not self._stop.is_set() and not self.stream._stopping.is_set():
                try:
                    frame = self.stream.next_frame(after_index=previous, timeout=0.2)
                except CaptureTimeoutError:
                    continue
                with self.stream._condition:
                    self.stream._metrics.increment(
                        "preview_skips", max(0, frame.metadata.frame_id - previous - 1)
                    )
                previous = frame.metadata.frame_id
                mosaic = decode(frame)
                spec = lookup(frame.metadata.pixel_format)
                # Downsample in 2x2 CFA blocks to preserve Bayer orientation before interpolation.
                step = max(1, (max(mosaic.shape) + self.max_dimension - 1) // self.max_dimension)
                if spec.cfa:
                    import numpy as np

                    h, w = mosaic.shape
                    if h % 2 or w % 2:
                        raise UnsupportedFeatureError("preview requires even Bayer dimensions")
                    reduced = mosaic.reshape(h // 2, 2, w // 2, 2)[::step, :, ::step, :]
                    mosaic = np.ascontiguousarray(reduced).reshape(
                        reduced.shape[0] * 2, reduced.shape[2] * 2
                    )
                    self.last_image = demosaic_rggb(mosaic)
                else:
                    self.last_image = mosaic[::step, ::step].copy()
        except CameraError as exc:
            if not self._stop.is_set() and not self.stream._stopping.is_set():
                self.error = exc
        except Exception as exc:
            self.error = exc

    def stop(self):
        self._stop.set()
        self._thread.join(1)

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.stop()
