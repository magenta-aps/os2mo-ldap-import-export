# SPDX-FileCopyrightText: 2019-2020 Magenta ApS
# SPDX-License-Identifier: MPL-2.0
"""Test conftest fixtures."""
import os

import pytest


@pytest.mark.parametrize(
    "environmental_variables,key,expected",
    [
        ({}, "VAR1", None),
        ({"VAR1": "1"}, "VAR1", "1"),
        ({"VAR1": "2"}, "VAR1", "2"),
        ({"VAR1": "2"}, "VAR2", None),
        ({"VAR1": "2", "VAR2": "2"}, "VAR2", "2"),
    ],
)
@pytest.mark.usefixtures("inject_environmental_variables")
def test_inject_envvars(key: str, expected: str) -> None:
    assert os.environ.get(key) == expected


@pytest.mark.envvar({"VAR1": "1", "VAR2": "2"})
@pytest.mark.envvar({"VAR3": "3"})
def test_load_marked_envvars() -> None:
    assert os.environ.get("VAR1") == "1"
    assert os.environ.get("VAR2") == "2"
    assert os.environ.get("VAR3") == "3"
    assert os.environ.get("VAR4") is None
