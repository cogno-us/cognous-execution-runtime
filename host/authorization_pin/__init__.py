"""Trusted host installer for the authorization pin (Registry r08-v2 Amendment 4). Candidate component; non-authorizing; HOLD."""
from .installer import (  # noqa: F401
    CustodyError, InstallerConfig, STAGES, InstallerError, InstallResult, PIN_EXPORT_PROFILE, TrustedAuthorizationInstaller,
)
