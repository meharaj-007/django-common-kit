"""The tag is the release artifact, so the version must agree everywhere."""

import os
import re
import unittest

import django_common_kit

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class VersionTests(unittest.TestCase):
    def test_changelog_names_the_current_version(self):
        changelog = open(os.path.join(ROOT, "CHANGELOG.md")).read()
        headings = re.findall(r"^## \[([^\]]+)\]", changelog, re.MULTILINE)
        self.assertTrue(headings, "CHANGELOG.md has no version headings")
        released = [h for h in headings if h != "Unreleased"]
        if not released:
            self.skipTest("nothing released yet; only [Unreleased] is present")
        self.assertEqual(released[0], django_common_kit.__version__)

    def test_readme_pins_the_current_version(self):
        path = os.path.join(ROOT, "README.md")
        if not os.path.exists(path):
            self.skipTest("README.md not written yet")
        pins = re.findall(r"django-common-kit(?:\.git)?(?:@v|==)([0-9.]+)", open(path).read())
        for pin in pins:
            self.assertEqual(pin, django_common_kit.__version__)
