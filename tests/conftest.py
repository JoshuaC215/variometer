"""Public test suite configuration.

Disable the user_data/profile.json overlay so the suite is deterministic
placeholder math for every contributor, regardless of what's in their private
user_data/ directory. Personal pinned regressions live in user_data/tests/
and run separately (``uv run pytest user_data/tests``) with the profile on.
"""

import os

os.environ["VARIOMETER_USER_DATA"] = ""

from variometer.user_data_io import (  # noqa: E402
    load_historical,
    load_profile,
    load_vest_grants,
)

load_historical.cache_clear()
load_profile.cache_clear()
load_vest_grants.cache_clear()
