"""Test package behaviour."""

import importlib


def test_package_is_importable() -> None:
    """Verify the generated package can be imported."""
    package = importlib.import_module("metrics")

    assert package.__doc__
