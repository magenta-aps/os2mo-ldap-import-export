# SPDX-FileCopyrightText: Magenta ApS <https://magenta.dk>
# SPDX-License-Identifier: MPL-2.0
import json
from collections.abc import Awaitable
from collections.abc import Callable
from unittest.mock import ANY
from uuid import UUID
from uuid import uuid4

import pytest
from ldap3 import Connection
from more_itertools import one

from mo_ldap_import_export.autogenerated_graphql_client import EmployeeCreateInput
from mo_ldap_import_export.autogenerated_graphql_client import GraphQLClient
from mo_ldap_import_export.autogenerated_graphql_client.input_types import (
    EngagementCreateInput,
)
from mo_ldap_import_export.autogenerated_graphql_client.input_types import (
    ManagerCreateInput,
)
from mo_ldap_import_export.ldap import get_ldap_object
from mo_ldap_import_export.ldap_classes import LdapObject
from mo_ldap_import_export.types import DN
from mo_ldap_import_export.types import EmployeeUUID


@pytest.mark.integration_test
@pytest.mark.envvar(
    {
        "LISTEN_TO_CHANGES_IN_MO": "False",
        "LISTEN_TO_CHANGES_IN_LDAP": "False",
        "CONVERSION_MAPPING": json.dumps(
            {
                "mo2ldap": """
                    {% set mo_employee_engagement = load_mo_primary_engagement(uuid) %}

                    {% set mo_manager_uuid = None %}
                    {% if mo_employee_engagement %}
                        {% set mo_manager_uuid = get_manager_person_uuid(mo_employee_engagement.uuid) %}
                    {% endif %}

                    {% set mo_manager_dn = None %}
                    {% if mo_manager_uuid %}
                        {% set mo_manager_dn = get_person_dn(mo_manager_uuid) %}
                    {% endif %}

                    {% set mo_employee = load_mo_employee(uuid, current_objects_only=False) %}
                    {{
                        {
                            "manager": mo_manager_dn if mo_manager_dn else [],
                            "employeeNumber": mo_employee.cpr_number,
                            "carLicense": mo_employee.uuid|string,
                            "cn": mo_employee.given_name + " " + mo_employee.surname,
                            "sn": mo_employee.surname,
                            "givenName": mo_employee.given_name,
                            "displayName": mo_employee.nickname_given_name + " " + mo_employee.nickname_surname
                        }|tojson
                    }}
                """,
                "username_generator": {
                    "combinations_to_try": ["FFFX", "LLLX"],
                },
            }
        ),
    }
)
async def test_write_manager_to_ldap(
    trigger_sync: Callable[[EmployeeUUID], Awaitable[None]],
    ldap_connection: Connection,
    ldap_person_dn: DN,
    graphql_client: GraphQLClient,
    mo_person: EmployeeUUID,
    mo_org_unit: UUID,
    ansat: UUID,
    jurist: UUID,
    primary: UUID,
) -> None:
    # We start off without a manager
    ldap_object = await get_ldap_object(ldap_connection, ldap_person_dn)
    assert ldap_object.dn == ldap_person_dn
    assert hasattr(ldap_object, "manager") is False

    # Forcefully synchronizing does not give us a manager
    await trigger_sync(mo_person)

    ldap_object = await get_ldap_object(ldap_connection, ldap_person_dn)
    assert ldap_object.dn == ldap_person_dn
    assert hasattr(ldap_object, "manager") is False

    # We construct a manager in MO
    await graphql_client.engagement_create(
        input=EngagementCreateInput(
            user_key="engagement",
            person=mo_person,
            org_unit=mo_org_unit,
            engagement_type=ansat,
            job_function=jurist,
            primary=primary,
            validity={"from": "2000-01-01T00:00:00Z"},
        )
    )
    manager_person = await graphql_client.person_create(
        input=EmployeeCreateInput(
            given_name="Boss",
            surname="Supervisor",
            cpr_number="0101701234",
        )
    )
    await graphql_client._testing__manager_create(
        ManagerCreateInput(
            user_key="manager",
            org_unit=mo_org_unit,
            responsibility=[],
            manager_level=uuid4(),
            manager_type=uuid4(),
            person=manager_person.uuid,
            validity={"from": "2000-01-01T00:00:00Z"},
        )
    )
    # Synchronizing the manager to create a LDAP account for them
    await trigger_sync(EmployeeUUID(manager_person.uuid))

    # Synchronizing our user to get the manager relation set
    await trigger_sync(mo_person)

    # Explicitly nesting to fetch manager object via its DN
    ldap_object = await get_ldap_object(ldap_connection, ldap_person_dn, nest=True)
    assert ldap_object.dn == ldap_person_dn
    assert hasattr(ldap_object, "manager") is True
    manager_object = one(ldap_object.manager)  # type: ignore
    assert isinstance(manager_object, LdapObject)
    assert manager_object.dict() == {
        "dn": ANY,
        "objectClass": ["inetOrgPerson"],
        "givenName": ["Boss"],
        "sn": ["Supervisor"],
        "cn": ["Boss Supervisor"],
        "displayName": " ",
        "employeeNumber": "0101701234",
        "carLicense": [str(manager_person.uuid)],
    }
