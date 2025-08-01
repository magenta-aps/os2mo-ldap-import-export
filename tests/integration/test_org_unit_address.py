# SPDX-FileCopyrightText: Magenta ApS <https://magenta.dk>
# SPDX-License-Identifier: MPL-2.0
import json
from typing import Any
from typing import cast
from uuid import UUID

import pytest
from fastramqpi.pytest_util import retrying
from more_itertools import one

from mo_ldap_import_export.autogenerated_graphql_client import AddressCreateInput
from mo_ldap_import_export.autogenerated_graphql_client import AddressUpdateInput
from mo_ldap_import_export.autogenerated_graphql_client import GraphQLClient
from mo_ldap_import_export.autogenerated_graphql_client.input_types import (
    AddressTerminateInput,
)
from mo_ldap_import_export.autogenerated_graphql_client.input_types import (
    EngagementCreateInput,
)
from mo_ldap_import_export.ldapapi import LDAPAPI
from mo_ldap_import_export.utils import combine_dn_strings
from mo_ldap_import_export.utils import mo_today


@pytest.mark.integration_test
@pytest.mark.envvar(
    {
        "LISTEN_TO_CHANGES_IN_MO": "True",
        "LISTEN_TO_CHANGES_IN_LDAP": "False",
        "CONVERSION_MAPPING": json.dumps(
            {
                "mo2ldap": """
                {% set mo_org_unit_address = load_mo_org_unit_address(uuid, "EmailUnit") %}
                {{
                    {
                        "mail": mo_org_unit_address.value if mo_org_unit_address else [],
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
    mo_org_unit: UUID,
    ldap_api: LDAPAPI,
    ldap_org_unit: list[str],
    ansat: UUID,
    jurist: UUID,
    primary: UUID,
    email_unit: UUID,
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
    await graphql_client.engagement_create(
        input=EngagementCreateInput(
            user_key="engagement",
            person=mo_person,
            org_unit=mo_org_unit,
            engagement_type=ansat,
            job_function=jurist,
            primary=primary,
            validity={"from": "2001-02-03T04:05:06Z"},
        )
    )
    # Create address
    mail = "create@example.com"
    mo_address = await graphql_client.address_create(
        input=AddressCreateInput(
            user_key="test address",
            address_type=email_unit,
            value=mail,
            org_unit=mo_org_unit,
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
            address_type=email_unit,
            org_unit=mo_org_unit,
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
        )
    )
    async for attempt in retrying():
        with attempt:
            assert await get_address() == {"mail": []}
