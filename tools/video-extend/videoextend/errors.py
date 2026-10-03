"""Error types for the video-extension feature.

Every error carries a message aimed at a non-technical user: say what went
wrong and what to do about it, not which function raised.
"""


class VideoExtendError(Exception):
    """Base class for all errors this module raises deliberately."""


class InvalidVideoError(VideoExtendError):
    """The input file is missing, unreadable, or not a usable video."""


class ToolMissingError(VideoExtendError):
    """A required external tool (ffmpeg/ffprobe) is not installed."""


class ProviderError(VideoExtendError):
    """The cloud provider failed, refused, or returned something unusable."""


class QuotaExceededError(ProviderError):
    """The cloud provider rejected the job because the free GPU quota ran out."""


class CancelledError(VideoExtendError):
    """The user cancelled the job."""
