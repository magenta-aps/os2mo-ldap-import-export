# SPDX-FileCopyrightText: Magenta ApS <https://magenta.dk>
# SPDX-License-Identifier: MPL-2.0
from uuid import UUID
from uuid import uuid4

import pytest

from mo_ldap_import_export.autogenerated_graphql_client.client import GraphQLClient
from mo_ldap_import_export.autogenerated_graphql_client.input_types import (
    EmployeeCreateInput,
)
from mo_ldap_import_export.environments.main import get_person_uuid


@pytest.mark.integration_test
async def test_get_person_uuid(graphql_client: GraphQLClient, mo_person: UUID) -> None:
    result = await get_person_uuid(graphql_client, filter={"uuids": [mo_person]})
    assert result == mo_person


@pytest.mark.integration_test
async def test_get_person_uuid_no_match(graphql_client: GraphQLClient) -> None:
    result = await get_person_uuid(graphql_client, filter={"uuids": [uuid4()]})
    assert result is None


@pytest.mark.integration_test
async def test_get_person_uuid_multiple_matches(graphql_client: GraphQLClient) -> None:
    e1 = await graphql_client.person_create(
        input=EmployeeCreateInput(
            given_name="Aage",
            surname="Bach Klarskov",
            cpr_number="2108613133",
        )
    )
    e1_uuid = e1.uuid
    e2 = await graphql_client.person_create(
        input=EmployeeCreateInput(
            given_name="Betina",
            surname="Bach Klarskov",
            cpr_number="2108613134",
        )
    )
    e2_uuid = e2.uuid

    with pytest.raises(ValueError) as exc_info:
        await get_person_uuid(graphql_client, filter={"uuids": [e1_uuid, e2_uuid]})
    assert "Expected exactly one item in iterable" in str(exc_info.value)
