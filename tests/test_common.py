import os
from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
COMMON = ROOT / "actions/_lib/common.sh"


class CommonTest(unittest.TestCase):
    def read_python_version(self, content):
        with tempfile.TemporaryDirectory(prefix="release action test ") as scratch:
            version_file = Path(scratch) / "__version__.py"
            version_file.write_text(content)
            result = subprocess.run(
                [
                    "bash", "-c",
                    'source "$1"; read_current_version python "$2" "" ""',
                    "test-common",
                    str(COMMON),
                    str(version_file),
                ],
                env={k: v for k, v in os.environ.items() if not k.startswith("GIT_")},
                capture_output=True,
                text=True,
                check=True,
            )
            return result.stdout.strip()

    def test_python_version_ignores_trailing_comment(self):
        content = '__version__ = "2.0.1"  # x-release-please-version\n'

        self.assertEqual(self.read_python_version(content), "2.0.1")

    def test_python_version_without_comment_is_unchanged(self):
        content = '__version__ = "2.0.1"\n'

        self.assertEqual(self.read_python_version(content), "2.0.1")
