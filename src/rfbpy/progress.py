"""Shared tqdm progress-bar configuration.

All of rfbpy's bars use :data:`BAR_FORMAT`: the description on the left, the
bar, and only the completed percentage (two decimals) on the right.
"""

BAR_FORMAT = "{desc}: {bar}| {percentage:3.2f}%"
