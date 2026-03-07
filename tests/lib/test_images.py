# SPDX-FileCopyrightText: 2025 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

import unittest

from terok.lib.core import images


class ImagesTests(unittest.TestCase):
    """Tests for the images module."""

    # Tests for _base_tag function

    def test_base_tag_simple_cases(self) -> None:
        """Simple input/expected _base_tag tests."""
        cases = [
            ("", "ubuntu-24.04", "empty string should return default tag"),
            ("   ", "ubuntu-24.04", "whitespace-only should return default tag"),
            ("ubuntu-22.04", "ubuntu-22.04", "simple valid name should be normalized"),
            ("Ubuntu-22.04", "ubuntu-22.04", "uppercase should be converted to lowercase"),
            ("ubuntu@22#04", "ubuntu-22-04", "special characters should be replaced with hyphens"),
            ("test@#$%^&*()image", "test-image", "multiple special characters should be replaced"),
            (
                "--ubuntu-22.04--",
                "ubuntu-22.04",
                "leading/trailing dots and hyphens should be stripped",
            ),
            ("ubuntu.22.04", "ubuntu.22.04", "dots in valid positions should be preserved"),
            ("ubuntu_22_04", "ubuntu_22_04", "underscores should be preserved"),
            (
                "ubuntu-22.04_LTS",
                "ubuntu-22.04_lts",
                "mixed valid characters should be preserved",
            ),
            ("@#$%^&*()", "ubuntu-24.04", "only special characters should return default"),
        ]
        for input_val, expected, description in cases:
            with self.subTest(input=input_val, expected=expected, msg=description):
                result = images._base_tag(input_val)
                self.assertEqual(result, expected)

    def test_base_tag_long_name_under_limit(self) -> None:
        """Long name under 120 chars should not be truncated."""
        name = "a" * 120
        result = images._base_tag(name)
        self.assertEqual(result, name)
        self.assertEqual(len(result), 120)

    def test_base_tag_long_name_over_limit(self) -> None:
        """Long name over 120 chars should be truncated with hash."""
        name = "a" * 121
        result = images._base_tag(name)
        # Should be 111 chars + "-" + 8 char hash = 120 total
        self.assertEqual(len(result), 120)
        self.assertTrue(result.startswith("a" * 111))
        self.assertTrue("-" in result[111:])
        # Check hash is alphanumeric
        hash_part = result.split("-")[-1]
        self.assertEqual(len(hash_part), 8)
        self.assertTrue(hash_part.isalnum())

    def test_base_tag_long_name_consistent_hash(self) -> None:
        """Same long name should produce same hash."""
        name = "b" * 150
        result1 = images._base_tag(name)
        result2 = images._base_tag(name)
        self.assertEqual(result1, result2)

    def test_base_tag_long_name_different_hash(self) -> None:
        """Different long names should produce different hashes."""
        name1 = "c" * 150
        name2 = "d" * 150
        result1 = images._base_tag(name1)
        result2 = images._base_tag(name2)
        self.assertNotEqual(result1, result2)

    def test_base_tag_long_with_special_chars(self) -> None:
        """Long name with special chars should be sanitized then truncated."""
        name = "ubuntu@special" * 20  # Over 120 chars with special chars
        result = images._base_tag(name)
        self.assertEqual(len(result), 120)
        # Should not contain @ symbols
        self.assertNotIn("@", result)

    # Tests for image naming functions

    def test_base_dev_image(self) -> None:
        """base_dev_image should return correct L0 image name and sanitize input."""
        cases = [
            ("ubuntu-22.04", "terok-l0:ubuntu-22.04", "normal input"),
            ("ubuntu@22.04", "terok-l0:ubuntu-22.04", "special chars sanitized"),
        ]
        for input_val, expected, description in cases:
            with self.subTest(input=input_val, expected=expected, msg=description):
                result = images.base_dev_image(input_val)
                self.assertEqual(result, expected)

    def test_agent_cli_image(self) -> None:
        """agent_cli_image should return correct L1 CLI image name and sanitize input."""
        cases = [
            ("ubuntu-22.04", "terok-l1-cli:ubuntu-22.04", "normal input"),
            ("ubuntu@22.04", "terok-l1-cli:ubuntu-22.04", "special chars sanitized"),
        ]
        for input_val, expected, description in cases:
            with self.subTest(input=input_val, expected=expected, msg=description):
                result = images.agent_cli_image(input_val)
                self.assertEqual(result, expected)

    def test_agent_ui_image(self) -> None:
        """agent_ui_image should return correct L1 UI image name and sanitize input."""
        cases = [
            ("ubuntu-22.04", "terok-l1-ui:ubuntu-22.04", "normal input"),
            ("ubuntu@22.04", "terok-l1-ui:ubuntu-22.04", "special chars sanitized"),
        ]
        for input_val, expected, description in cases:
            with self.subTest(input=input_val, expected=expected, msg=description):
                result = images.agent_ui_image(input_val)
                self.assertEqual(result, expected)

    def test_project_images(self) -> None:
        """Project image functions should return correct L2 image names."""
        cases = [
            (images.project_cli_image, "my-project", "my-project:l2-cli", "CLI image"),
            (images.project_web_image, "my-project", "my-project:l2-web", "web image"),
            (images.project_dev_image, "my-project", "my-project:l2-dev", "dev image"),
        ]
        for func, input_val, expected, description in cases:
            with self.subTest(func=func.__name__, expected=expected, msg=description):
                result = func(input_val)
                self.assertEqual(result, expected)

    def test_all_functions_with_empty_base_image(self) -> None:
        """All base_image functions should handle empty string."""
        self.assertEqual(images.base_dev_image(""), "terok-l0:ubuntu-24.04")
        self.assertEqual(images.agent_cli_image(""), "terok-l1-cli:ubuntu-24.04")
        self.assertEqual(images.agent_ui_image(""), "terok-l1-ui:ubuntu-24.04")

    def test_all_functions_with_long_base_image(self) -> None:
        """All base_image functions should handle long names."""
        long_name = "x" * 150
        # All should produce 120-char tags
        base_dev = images.base_dev_image(long_name)
        agent_cli = images.agent_cli_image(long_name)
        agent_ui = images.agent_ui_image(long_name)

        # Extract tags (after the colon)
        base_dev_tag = base_dev.split(":")[1]
        agent_cli_tag = agent_cli.split(":")[1]
        agent_ui_tag = agent_ui.split(":")[1]

        self.assertEqual(len(base_dev_tag), 120)
        self.assertEqual(len(agent_cli_tag), 120)
        self.assertEqual(len(agent_ui_tag), 120)

        # All should use the same tag
        self.assertEqual(base_dev_tag, agent_cli_tag)
        self.assertEqual(agent_cli_tag, agent_ui_tag)


if __name__ == "__main__":
    unittest.main()
