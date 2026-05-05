# SPDX-FileCopyrightText: Magenta ApS <https://magenta.dk>
# SPDX-License-Identifier: MPL-2.0
"""Prove that ldap_to_mo[_any] field evaluation does not follow input order."""

import json

import pytest
from fastramqpi.context import Context
from httpx import AsyncClient
from more_itertools import one

from mo_ldap_import_export.config import Settings
from mo_ldap_import_export.types import LDAPUUID


@pytest.mark.integration_test
@pytest.mark.envvar(
    {
        "LISTEN_TO_CHANGES_IN_LDAP": "False",
        "LISTEN_TO_CHANGES_IN_MO": "False",
        "CONVERSION_MAPPING": json.dumps(
            {
                "ldap_to_mo": {
                    "ITUser": {
                        "objectClass": "ITUser",
                        "_import_to_mo_": "true",
                        "_ldap_attributes_": [],
                        "uuid": "{{ uuid4() }}",
                        "user_key": "stable",
                        "person": "{{ skip_if_none(none) }}",
                        "itsystem": "{{ requeue_if_none(none) }}",
                    },
                }
            }
        ),
    }
)
@pytest.mark.usefixtures("ldap_person", "mo_person")
async def test_ldap_to_mo_sync_behavior_does_not_follow_input_order(
    test_client: AsyncClient,
    context: Context,
    ldap_person_uuid: LDAPUUID,
) -> None:
    # This intentionally fetches the setting used by FastRAMQPI instead of constructing
    # one directly, as the following for-loop modifies it by reference to reproduce a
    # flaky production issue.
    settings: Settings = context["user_context"]["settings"]
    assert settings.conversion_mapping.ldap_to_mo is not None
    mapping = settings.conversion_mapping.ldap_to_mo["ITUser"]

    # Move `itsystem` (requeue) to be before `person` (skip) in the Pydantic dict order.
    # This order ensures that the synchronization yields requeueing instead of skipping
    # behavior, by reproducing an issue from Pydantic v1 where `Extra.allow` fields are
    # collected via set-iteration and thus ordered by `hash` subject to PYTHONHASHSEED.
    # Thus the order forced here is only introduced for determinism, as without it
    # the test becomes flaky (subject to the PYTHONHASHSEED generated at start-up).
    # To see it fail without this code, simply comment out the for-loop and run:
    #
    #     PYTHONHASHSEED=5 pytest tests/integration/test_mapping_order_randomization.py
    for key in ["itsystem", "person"]:
        mapping.__dict__[key] = mapping.__dict__.pop(key)

    # Trigger synchronization and see that requeue is raised instead of skipping
    response = await test_client.post(
        "/ldap2mo/uuid",
        json={"subject": str(ldap_person_uuid), "priority": 1},
    )
    assert response.status_code == 409, response.text
    assert response.json() == {"detail": "Requeueing: Object is None"}


@pytest.mark.integration_test
@pytest.mark.envvar(
    {
        "LISTEN_TO_CHANGES_IN_LDAP": "False",
        "LISTEN_TO_CHANGES_IN_MO": "False",
        "CONVERSION_MAPPING": json.dumps(
            {
                "ldap_to_mo_any": {
                    "inetOrgPerson": [
                        {
                            "objectClass": "ITUser",
                            "_import_to_mo_": "true",
                            "_ldap_attributes_": [],
                            "uuid": "{{ uuid4() }}",
                            "user_key": "stable",
                            "person": "{{ skip_if_none(none) }}",
                            "itsystem": "{{ requeue_if_none(none) }}",
                        },
                    ]
                }
            }
        ),
    }
)
@pytest.mark.usefixtures("ldap_person", "mo_person")
async def test_ldap_to_mo_any_sync_behavior_does_not_follow_input_order(
    test_client: AsyncClient,
    context: Context,
    ldap_person_uuid: LDAPUUID,
) -> None:
    # See the ldap_to_mo test above for why settings are extracted like this, and why
    # the below for-loop exists.
    settings: Settings = context["user_context"]["settings"]
    mapping = one(settings.conversion_mapping.ldap_to_mo_any["inetOrgPerson"])
    for key in ["itsystem", "person"]:
        mapping.__dict__[key] = mapping.__dict__.pop(key)

    # Trigger synchronization and see that requeue is raised instead of skipping
    response = await test_client.post(
        "/ldap2mo/uuid",
        json={"subject": str(ldap_person_uuid), "priority": 1},
    )
    assert response.status_code == 409, response.text
    assert response.json() == {"detail": "Requeueing: Object is None"}
