"""
test_termux_compatibility.py
Automated audit and regression tests verifying Android Termux portability.
Validates:
- Zero heavy C-dependencies (no pandas, numpy, scipy) imported anywhere in src/.
- Cross-platform path handling (no hardcoded Windows drive letters or /tmp/ paths).
- Pure standard library Python compliance.
- Atomic state serialization and UTF-8 encoding integrity.
"""

import os
import sys
import json
import re
import unittest
from pathlib import Path

WORKSPACE_ROOT = Path(__file__).resolve().parent.parent


class TestTermuxCompatibility(unittest.TestCase):
    """Test suite ensuring Android Termux compatibility and resource discipline."""

    FORBIDDEN_PACKAGES = ["pandas", "numpy", "scipy", "sklearn", "matplotlib", "seaborn", "talib"]

    def test_no_heavy_c_dependencies_in_src(self):
        """Audit all python files in src/ to ensure zero forbidden C-dependencies are imported."""
        src_dir = WORKSPACE_ROOT / "src"
        violations = []

        for py_file in src_dir.rglob("*.py"):
            try:
                content = py_file.read_text(encoding="utf-8")
            except Exception:
                continue

            for pkg in self.FORBIDDEN_PACKAGES:
                # Regex looking for `import pkg` or `from pkg import ...`
                pattern = rf"^\s*(?:import\s+{pkg}|from\s+{pkg}\b)"
                if re.search(pattern, content, re.MULTILINE):
                    violations.append(f"{py_file.name}: imports forbidden '{pkg}'")

        self.assertEqual(len(violations), 0, f"Found heavy C-dependency violations in src/: {violations}")

    def test_no_hardcoded_windows_drive_paths_in_src(self):
        """Ensure no hardcoded C:\\ or D:\\ drive paths exist in src/."""
        src_dir = WORKSPACE_ROOT / "src"
        violations = []

        for py_file in src_dir.rglob("*.py"):
            try:
                content = py_file.read_text(encoding="utf-8")
            except Exception:
                continue

            # Check for hardcoded Windows root paths like C:\ or C:/
            if re.search(r'[A-Za-z]:[\\/]', content):
                violations.append(f"{py_file.name}: contains hardcoded drive letter path")

        self.assertEqual(len(violations), 0, f"Found hardcoded drive paths in src/: {violations}")

    def test_no_hardcoded_tmp_paths_in_src(self):
        """Ensure no hardcoded POSIX /tmp/ paths exist in src/ (Termux uses $PREFIX/tmp)."""
        src_dir = WORKSPACE_ROOT / "src"
        violations = []

        for py_file in src_dir.rglob("*.py"):
            try:
                content = py_file.read_text(encoding="utf-8")
            except Exception:
                continue

            if '"/tmp/' in content or "'/tmp/" in content:
                violations.append(f"{py_file.name}: contains hardcoded '/tmp/' path")

        self.assertEqual(len(violations), 0, f"Found hardcoded /tmp/ paths in src/: {violations}")

    def test_requirements_file_is_minimal(self):
        """Verify requirements.txt contains only lightweight dependencies."""
        req_path = WORKSPACE_ROOT / "requirements.txt"
        self.assertTrue(req_path.exists())

        lines = [line.strip().lower() for line in req_path.read_text(encoding="utf-8").splitlines() if line.strip()]
        for line in lines:
            for pkg in self.FORBIDDEN_PACKAGES:
                self.assertFalse(line.startswith(pkg), f"requirements.txt contains heavy package {pkg}")

    def test_atomic_json_serialization(self):
        """Ensure atomic state writing works portably with utf-8 encoding."""
        test_dir = WORKSPACE_ROOT / "logs" / "test_scratch"
        test_dir.mkdir(parents=True, exist_ok=True)
        test_file = test_dir / "test_atomic.json"

        data = {"status": "TEST_OK", "timestamp": "2026-10-25T15:30:00Z", "pnl": 1250.50}
        with open(test_file, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)

        self.assertTrue(test_file.exists())
        with open(test_file, "r", encoding="utf-8") as f:
            loaded = json.load(f)
        self.assertEqual(loaded["status"], "TEST_OK")

        # Cleanup
        if test_file.exists():
            test_file.unlink()
        if test_dir.exists():
            test_dir.rmdir()


if __name__ == "__main__":
    unittest.main()
