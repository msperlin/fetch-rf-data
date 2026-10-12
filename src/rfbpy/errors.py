"""Exception hierarchy for :mod:`rfbpy`."""


class RFBError(Exception):
    """Base class for every error raised by rfbpy."""


class ConfigError(RFBError, ValueError):
    """Raised when the configuration is missing or has invalid values."""


class ShareAccessError(RFBError, RuntimeError):
    """Raised when the Receita Federal public share cannot be read."""


class DownloadError(RFBError):
    """Raised for unrecoverable download errors."""
