"""Single source of truth for product naming. Rename the product here.

The Python package, the environment variables (``AGENTFIT_HOME`` and friends) and the
database file keep the project's first name, ``agentfit``: they are internal, and
renaming them would only break existing setups.
"""

APP_NAME = "Will It Run"
APP_SLUG = "willitrun"
APP_VERSION = "0.1.0"
APP_TAGLINE = "What AI team can this PC run?"
APP_VENDOR = "Will It Run"
CLI_NAME = "willitrun"
SIDECAR_EXE = "willitrun-core"
# A product token may not contain spaces.
USER_AGENT = f"WillItRun/{APP_VERSION}"

# The name the app shipped under before. Its data folders are carried over on first start.
LEGACY_APP_NAME = "AgentFit"
