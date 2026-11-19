"""Centralized brand and runtime identity constants for EIDOLON."""
from __future__ import annotations

from pathlib import Path

APP_NAME = "EIDOLON"
APP_SLUG = "eidolon"
APP_VERSION = "1.0.0"
APP_AUTHOR = "Aymerick (@AYMRIKNG)"
APP_REPO = "https://github.com/AYMRIKNG/EIDOLON"

BASE_DIR = Path(__file__).resolve().parent.parent

# Storage & integration identifiers
PROFILES_DIR_NAME = ".eidolon_profiles"
PROJECTS_DIR_NAME = "EidolonProjects"
UPLOADS_DIR_NAME = "EIDOLON Uploads"
DASHBOARD_AES_SALT = b"EIDOLON-DASHBOARD-v1"
REVIEW_PORT_DEFAULT = 47321
