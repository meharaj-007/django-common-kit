"""Nothing under ``django_common_utils/`` may import a host project module (PRD §2).

The failure this prevents is not a crash — it is worse than that. An
``import base.settings`` works perfectly in the project the code was written
for and raises ``ModuleNotFoundError`` in the next one, so the package appears
to work until somebody installs it somewhere new. That is why the rule is a
test rather than a note.
"""

import ast
import os
import unittest

PACKAGE_ROOT = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "django_common_utils"
)

#: Top-level package names that belong to a consuming project, never to this one.
FORBIDDEN_ROOTS = {
    "base", "common", "config", "project",
    "user_control", "order_control", "email_control", "notification_control",
    "job_control", "service_control", "marketing_control", "complaint_control",
    "location_control", "lead_control", "crm_control",
}


def _source_files():
    for dirpath, _dirnames, filenames in os.walk(PACKAGE_ROOT):
        if "__pycache__" in dirpath:
            continue
        for name in filenames:
            if name.endswith(".py"):
                yield os.path.join(dirpath, name)


class ImportPurityTests(unittest.TestCase):
    def test_no_host_project_imports(self):
        offences = []
        for path in _source_files():
            tree = ast.parse(open(path).read(), filename=path)
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    roots = [alias.name.split(".")[0] for alias in node.names]
                elif isinstance(node, ast.ImportFrom):
                    # level > 0 is a relative import — always our own.
                    if node.level:
                        continue
                    roots = [(node.module or "").split(".")[0]]
                else:
                    continue
                for root in roots:
                    if root in FORBIDDEN_ROOTS:
                        rel = os.path.relpath(path, PACKAGE_ROOT)
                        offences.append(f"{rel}:{node.lineno} imports {root!r}")
        self.assertEqual(offences, [], "host project imports found:\n" + "\n".join(offences))
