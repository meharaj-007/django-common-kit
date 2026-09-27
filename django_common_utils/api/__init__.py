"""The REST surface (PRD §5).

Nothing in here is imported at package import time — ``django_common_utils.api`` is a
namespace, and pulling ``response`` in from this module would drag DRF's settings
machinery into ``INSTALLED_APPS`` evaluation. Import the module you want.
"""
