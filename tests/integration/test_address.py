# SPDX-FileCopyrightText: Magenta ApS <https://magenta.dk>
# SPDX-License-Identifier: MPL-2.0
import json
from collections.abc import Awaitable
from collections.abc import Callable
from typing import Any
from typing import cast
from unittest.mock import ANY
from uuid import UUID

import pytest
from fastramqpi.pytest_util import retrying
from more_itertools import one

from mo_ldap_import_export.autogenerated_graphql_client import AddressCreateInput
from mo_ldap_import_export.autogenerated_graphql_client import AddressFilter
from mo_ldap_import_export.autogenerated_graphql_client import AddressTerminateInput
from mo_ldap_import_export.autogenerated_graphql_client import AddressUpdateInput
from mo_ldap_import_export.autogenerated_graphql_client import EmployeeFilter
from mo_ldap_import_export.autogenerated_graphql_client import GraphQLClient
from mo_ldap_import_export.ldapapi import LDAPAPI
from mo_ldap_import_export.utils import combine_dn_strings
from mo_ldap_import_export.utils import mo_today


@pytest.mark.integration_test
@pytest.mark.envvar(
    {
        "LISTEN_TO_CHANGES_IN_MO": "False",
        "LISTEN_TO_CHANGES_IN_LDAP": "True",
        "CONVERSION_MAPPING": json.dumps(
            {
                "ldap_to_mo": {
                    "Employee": {
                        "objectClass": "Employee",
                        "_import_to_mo_": "false",
                        "_ldap_attributes_": [],
                        "uuid": "{{ employee_uuid or '' }}",
                    },
                    "EmailEmployee": {
                        "objectClass": "Address",
                        "_import_to_mo_": "true",
                        "_ldap_attributes_": ["carLicense", "mail"],
                        # carLicense is arbitrarily chosen as an enabled/disabled marker
                        "_terminate_": "{{ now()|mo_datestring if ldap.carLicense == 'EXPIRED' else '' }}",
                        "uuid": "{{ get_address_uuid({'address_type': {'user_key': 'EmailEmployee'}, 'employee': {'uuids': [employee_uuid]}}) }}",
                        "value": "{{ ldap.mail }}",
                        "address_type": "{{ get_employee_address_type_uuid('EmailEmployee') }}",
                        "person": "{{ employee_uuid }}",
                        "visibility": "{{ get_visibility_uuid('Public') }}",
                    },
                },
                # TODO: why is this required?
                "username_generator": {
                    "combinations_to_try": ["FFFX", "LLLX"],
                },
            }
        ),
    }
)
@pytest.mark.usefixtures("test_client")
async def test_to_mo(
    graphql_client: GraphQLClient,
    mo_person: UUID,
    ldap_api: LDAPAPI,
    ldap_org_unit: list[str],
) -> None:
    async def get_address() -> dict[str, Any]:
        addresses = await graphql_client._testing__address_read(
            filter=AddressFilter(
                employee=EmployeeFilter(uuids=[mo_person]),
            ),
        )
        address = one(addresses.objects)
        validities = one(address.validities)
        return validities.dict()

    person_dn = combine_dn_strings(["uid=abk"] + ldap_org_unit)

    # LDAP: Create
    mail = "create@example.com"
    await ldap_api.ldap_connection.ldap_add(
        dn=person_dn,
        object_class=["top", "person", "organizationalPerson", "inetOrgPerson"],
        attributes={
            "objectClass": ["top", "person", "organizationalPerson", "inetOrgPerson"],
            "ou": "os2mo",
            "cn": "Aage Bach Klarskov",
            "sn": "Bach Klarskov",
            "employeeNumber": "2108613133",
            "mail": mail,
            "carLicense": "ACTIVE",
        },
    )
    mo_address = {
        "uuid": ANY,
        "user_key": ANY,
        "address_type": {"user_key": "EmailEmployee"},
        "value": mail,
        "value2": None,
        "person": [{"uuid": mo_person}],
        "visibility": {"user_key": "Public"},
        "validity": {"from_": mo_today(), "to": None},
    }
    async for attempt in retrying():
        with attempt:
            assert await get_address() == mo_address

    # LDAP: Edit
    mail = "edit@example.com"
    await ldap_api.ldap_connection.ldap_modify(
        dn=person_dn,
        changes={
            "mail": [("MODIFY_REPLACE", mail)],
        },
    )
    mo_address = {
        **mo_address,
        "value": mail,
    }
    async for attempt in retrying():
        with attempt:
            assert await get_address() == mo_address

    # LDAP: Terminate
    await ldap_api.ldap_connection.ldap_modify(
        dn=person_dn,
        changes={
            "carLicense": [("MODIFY_REPLACE", "EXPIRED")],
        },
    )
    mo_address = {
        **mo_address,
        "validity": {"from_": mo_today(), "to": mo_today()},
    }
    async for attempt in retrying():
        with attempt:
            assert await get_address() == mo_address


@pytest.mark.integration_test
@pytest.mark.envvar(
    {
        "LISTEN_TO_CHANGES_IN_MO": "False",
        "LISTEN_TO_CHANGES_IN_LDAP": "True",
        "CONVERSION_MAPPING": json.dumps(
            {
                "ldap_to_mo": {
                    "Employee": {
                        "objectClass": "Employee",
                        "_import_to_mo_": "false",
                        "_ldap_attributes_": [],
                        "uuid": "{{ employee_uuid or '' }}",
                    },
                    "EmailEmployee": {
                        "objectClass": "Address",
                        "_import_to_mo_": "true",
                        "_ldap_attributes_": ["mail"],
                        "_terminate_": "{{ now()|mo_datestring }}",
                        "uuid": "{{ get_address_uuid({'address_type': {'user_key': 'EmailEmployee'}, 'employee': {'uuids': [employee_uuid]}}) }}",
                        "value": "{{ ldap.mail }}",
                        "address_type": "{{ get_employee_address_type_uuid('EmailEmployee') }}",
                        "person": "{{ employee_uuid }}",
                        "visibility": "{{ get_visibility_uuid('Public') }}",
                    },
                },
                # TODO: why is this required?
                "username_generator": {
                    "combinations_to_try": ["FFFX", "LLLX"],
                },
            }
        ),
    }
)
@pytest.mark.usefixtures("test_client")
async def test_terminate_on_create(
    graphql_client: GraphQLClient,
    mo_person: UUID,
    trigger_ldap_person: Callable[[], Awaitable[None]],
) -> None:
    await trigger_ldap_person()

    addresses = await graphql_client._testing__address_read(
        filter=AddressFilter(
            employee=EmployeeFilter(uuids=[mo_person]),
        ),
    )
    assert not addresses.objects


@pytest.mark.integration_test
@pytest.mark.envvar(
    {
        "LISTEN_TO_CHANGES_IN_MO": "True",
        "LISTEN_TO_CHANGES_IN_LDAP": "False",
        "CONVERSION_MAPPING": json.dumps(
            {
                "mo2ldap": """
                {% set mo_employee_address = load_mo_address(uuid, "EmailEmployee") %}
                {{
                    {
                        "mail": mo_employee_address.value if mo_employee_address else [],
                    }|tojson
                }}
                """,
                # TODO: why is this required?
                "username_generator": {
                    "combinations_to_try": ["FFFX", "LLLX"],
                },
            }
        ),
    }
)
@pytest.mark.usefixtures("test_client")
async def test_to_ldap(
    graphql_client: GraphQLClient,
    mo_person: UUID,
    ldap_api: LDAPAPI,
    ldap_org_unit: list[str],
    email_employee: UUID,
    public: UUID,
) -> None:
    cpr = "2108613133"

    async def get_address() -> dict[str, Any]:
        response, _ = await ldap_api.ldap_connection.ldap_search(
            search_base=combine_dn_strings(ldap_org_unit),
            search_filter=f"(employeeNumber={cpr})",
            attributes=["mail"],
        )
        return cast(dict[str, Any], one(response)["attributes"])

    # LDAP: Init user
    person_dn = combine_dn_strings(["uid=abk"] + ldap_org_unit)
    await ldap_api.ldap_connection.ldap_add(
        dn=person_dn,
        object_class=["top", "person", "organizationalPerson", "inetOrgPerson"],
        attributes={
            "objectClass": ["top", "person", "organizationalPerson", "inetOrgPerson"],
            "ou": "os2mo",
            "cn": "Aage Bach Klarskov",
            "sn": "Bach Klarskov",
            "employeeNumber": cpr,
        },
    )
    async for attempt in retrying():
        with attempt:
            assert await get_address() == {"mail": []}

    # MO: Create
    mail = "create@example.com"
    mo_address = await graphql_client.address_create(
        input=AddressCreateInput(
            user_key="test address",
            address_type=email_employee,
            value=mail,
            person=mo_person,
            visibility=public,
            validity={"from": "2001-02-03T04:05:06Z"},
        )
    )
    async for attempt in retrying():
        with attempt:
            assert await get_address() == {"mail": [mail]}

    # MO: Edit
    mail = "update@example.com"
    await graphql_client.address_update(
        input=AddressUpdateInput(
            uuid=mo_address.uuid,
            value=mail,
            validity={"from": "2011-12-13T14:15:16Z"},
            # TODO: why is this required?
            user_key="test address",
            address_type=email_employee,
            person=mo_person,
            visibility=public,
        )
    )
    async for attempt in retrying():
        with attempt:
            assert await get_address() == {"mail": [mail]}

    # MO: Terminate
    await graphql_client.address_terminate(
        input=AddressTerminateInput(
            uuid=mo_address.uuid,
            to=mo_today(),
        ),
    )
    async for attempt in retrying():
        with attempt:
            assert await get_address() == {"mail": []}


@pytest.mark.integration_test
@pytest.mark.envvar(
    {
        "LISTEN_TO_CHANGES_IN_MO": "False",
        "LISTEN_TO_CHANGES_IN_LDAP": "False",
        "CONVERSION_MAPPING": json.dumps(
            {
                "ldap_to_mo": {
                    "Employee": {
                        "objectClass": "Employee",
                        "_import_to_mo_": "false",
                        "_ldap_attributes_": [],
                        "uuid": "{{ employee_uuid or '' }}",
                    },
                    "EmailEmployee": {
                        "objectClass": "Address",
                        "_import_to_mo_": "true",
                        "_ldap_attributes_": ["carLicense", "mail"],
                        # carLicense is arbitrarily chosen as an enabled/disabled marker
                        "_terminate_": "{{ now()|mo_datestring if ldap.carLicense == 'EXPIRED' else '' }}",
                        "uuid": "{{ get_address_uuid({'address_type': {'user_key': 'EmailEmployee'}, 'employee': {'uuids': [employee_uuid]}}) }}",
                        "value": "{{ ldap.mail }}",
                        "address_type": "{{ get_employee_address_type_uuid('EmailEmployee') }}",
                        "person": "{{ employee_uuid }}",
                        "visibility": "{{ get_visibility_uuid('Public') }}",
                    },
                },
                # TODO: why is this required?
                "username_generator": {
                    "combinations_to_try": ["FFFX", "LLLX"],
                },
            }
        ),
    }
)
@pytest.mark.usefixtures("test_client")
async def test_to_mo_terminate_without_value(
    graphql_client: GraphQLClient,
    mo_person: UUID,
    ldap_api: LDAPAPI,
    ldap_org_unit: list[str],
    trigger_ldap_person: Callable[[], Awaitable[None]],
) -> None:
    async def assert_address(expected: dict) -> None:
        addresses = await graphql_client._testing__address_read(
            filter=AddressFilter(
                employee=EmployeeFilter(uuids=[mo_person]),
            ),
        )
        address = one(addresses.objects)
        validities = one(address.validities)
        assert validities.dict() == expected

    person_dn = combine_dn_strings(["uid=abk"] + ldap_org_unit)

    mo_address = {
        "uuid": ANY,
        "user_key": ANY,
        "address_type": {"user_key": "EmailEmployee"},
        "value": "abk@ad.kolding.dk",
        "value2": None,
        "person": [{"uuid": mo_person}],
        "visibility": {"user_key": "Public"},
        "validity": {"from_": mo_today(), "to": None},
    }
    await trigger_ldap_person()
    await assert_address(mo_address)

    # Remove mail from the LDAP entry
    await ldap_api.ldap_connection.ldap_modify(
        dn=person_dn,
        changes={
            "mail": [("MODIFY_REPLACE", [])],
        },
    )
    # We expect the synchronization to fail
    with pytest.raises(AssertionError) as exc_info:
        await trigger_ldap_person()
    assert "Missing values in LDAP to synchronize" in str(exc_info.value)
    # We expect that the address was not modified
    await assert_address(mo_address)

    # Terminate the address
    await ldap_api.ldap_connection.ldap_modify(
        dn=person_dn,
        changes={
            "carLicense": [("MODIFY_REPLACE", "EXPIRED")],
        },
    )
    # We expect the address to have been terminated, although the value was missing
    await trigger_ldap_person()
    mo_address = {
        **mo_address,
        "validity": {"from_": mo_today(), "to": mo_today()},
    }
    await assert_address(mo_address)
