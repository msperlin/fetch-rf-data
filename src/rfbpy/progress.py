"""Shared tqdm progress-bar configuration.

All of rfbpy's bars use :data:`BAR_FORMAT`: the description on the left and a
right-aligned completed percentage (two decimals) on the right — no bar,
counter or timing.
"""

BAR_FORMAT = "{desc}: {percentage:6.2f}%"
