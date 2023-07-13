"""
Constants for Comwatt integration.
"""

from datetime import timedelta

DOMAIN = "comwatt"

PLATFORMS = ["sensor"]

SCAN_INTERVAL = timedelta(minutes=1)

ATTRIBUTION = "Comwatt Data"
