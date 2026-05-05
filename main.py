#!/usr/bin/env python
"""
Loglife Desktop Application - Entry Point

Desktop application for report management with Azure AD authentication.
"""

import sys

from src.bootstrap_env import load_dotenv_from_app_dir

load_dotenv_from_app_dir()

from src.app import main

if __name__ == '__main__':
    sys.exit(main())
