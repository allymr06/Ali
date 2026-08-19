from __future__ import annotations

import pytest

from app.core.identity import Identity


def test_identity_defaults_to_jarvis() -> None:
    identity = Identity()

    assert identity.system_name == "JARVIS"
    assert identity.current_name is None
    assert identity.display_name == "JARVIS"
    assert identity.describe() == "Ben JARVIS."


def test_identity_can_be_renamed() -> None:
    identity = Identity()

    identity.rename("TEST-NAME")

    assert identity.system_name == "JARVIS"
    assert identity.current_name == "TEST-NAME"
    assert identity.display_name == "TEST-NAME"
    assert (
        identity.describe()
        == "Ben TEST-NAME. Sistem kimliğim JARVIS."
    )


def test_identity_can_clear_current_name() -> None:
    identity = Identity()
    identity.rename("TEST-NAME")

    identity.clear_current_name()

    assert identity.current_name is None
    assert identity.display_name == "JARVIS"
    assert identity.describe() == "Ben JARVIS."


def test_identity_rejects_empty_system_name() -> None:
    with pytest.raises(ValueError):
        Identity(system_name="")


def test_identity_normalizes_names() -> None:
    identity = Identity(
        system_name="  JARVIS  ",
        current_name="  TEST-NAME  ",
    )

    assert identity.system_name == "JARVIS"
    assert identity.current_name == "TEST-NAME"


def test_identity_empty_current_name_becomes_unset() -> None:
    identity = Identity(current_name="   ")

    assert identity.current_name is None
    assert identity.display_name == "JARVIS"


def test_identity_rejects_empty_rename() -> None:
    identity = Identity()

    with pytest.raises(ValueError):
        identity.rename("   ")