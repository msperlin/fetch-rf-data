"""Shared tqdm progress-bar configuration.

All of rfbpy's bars use :data:`BAR_FORMAT`, which renders the completed
percentage with two decimals (e.g. ``45.32%``).
"""

BAR_FORMAT = (
    "{desc}: {percentage:3.2f}%|{bar}| {n_fmt}/{total_fmt} "
    "[{elapsed}<{remaining}, {rate_fmt}{postfix}]"
)
