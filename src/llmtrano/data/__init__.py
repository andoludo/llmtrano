"""Data files shipped with the package."""

from importlib.resources import files


def tutorial_house_yaml() -> str:
    """trano's tutorial house (`tests/tutorials/first_model.yaml`, the same from trano 4220d3c to 0.56.0)."""
    return files(__package__).joinpath("first_model.yaml").read_text()
