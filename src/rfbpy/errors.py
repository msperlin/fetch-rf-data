"""Exception hierarchy for :mod:`rfbpy`."""


class RFBError(Exception):
    """Base class for every error raised by rfbpy."""


class ConfigError(RFBError, ValueError):
    """Raised when the configuration is missing or has invalid values."""


class ShareAccessError(RFBError, RuntimeError):
    """Raised when the Receita Federal public share cannot be read."""


class DownloadError(RFBError):
    """Raised for unrecoverable download errors."""


class UnavailableFilesError(RFBError, ValueError):
    """Raised when a requested ``files_to_download`` entry is not published.

    The message lists the missing entries, the files that *are* available and
    how to fix the request.
    """

    def __init__(self, unmatched, available, month=None, base_url=None, max_listed=40):
        self.unmatched = [str(name) for name in unmatched]
        self.available = sorted({
            str(item.get("name") if isinstance(item, dict) else item)
            for item in available
        })
        self.month = month
        self.base_url = base_url
        super().__init__(self._build_message(max_listed))

    def _build_message(self, max_listed):
        lines = [
            "The following requested file(s) are not available on the share: "
            + ", ".join(self.unmatched)
            + ".",
        ]
        if self.month is not None:
            lines.append(f"(month: {self.month})")
        if self.available:
            shown = self.available[:max_listed]
            suffix = "" if len(shown) == len(self.available) else f", … (+{len(self.available) - len(shown)} more)"
            lines.append(f"Available .zip files ({len(self.available)}): {', '.join(shown)}{suffix}")
        else:
            lines.append("No .zip files were found in that folder.")
        lines.extend([
            "How to fix:",
            "  - Correct or remove the missing entries from `files_to_download`.",
            "    Entries are matched case-insensitively as substrings of the file names,",
            "    e.g. \"Estabelecimentos\" matches Estabelecimentos0.zip … Estabelecimentos9.zip.",
            "  - Inspect what is published: RFBData.list_months() and check(month=\"YYYY-MM\").",
            "  - To try a subset without editing the config, pass only=... (e.g. only=\"Cnaes,Paises\").",
        ])
        return "\n".join(lines)

