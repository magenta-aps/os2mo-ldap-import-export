# SPDX-FileCopyrightText: 2019-2020 Magenta ApS
# SPDX-License-Identifier: MPL-2.0
"""Integration tests."""
from unittest.mock import AsyncMock

import pytest
from fastramqpi.context import Context
from fastramqpi.pytest_util import retry

from mo_ldap_import_export.autogenerated_graphql_client import GraphQLClient
from mo_ldap_import_export.autogenerated_graphql_client.input_types import (
    ITSystemCreateInput,
)
from mo_ldap_import_export.autogenerated_graphql_client.input_types import (
    ITUserCreateInput,
)
from mo_ldap_import_export.autogenerated_graphql_client.input_types import (
    RAOpenValidityInput,
)
from mo_ldap_import_export.autogenerated_graphql_client.input_types import (
    RAValidityInput,
)


@pytest.mark.integration_test
@pytest.mark.usefixtures("test_client")
async def test_process_person(
    graphql_client: GraphQLClient,
    context: Context,
) -> None:
    sync_tool_mock = AsyncMock()
    context["user_context"]["sync_tool"] = sync_tool_mock

    @retry()
    async def verify(person_uuid) -> None:
        sync_tool_mock.listen_to_changes_in_employees.assert_called_with(person_uuid)

    # Create a person and verify that it ends up calling listen_to_changes_in_employees
    person_result = await graphql_client._testing_user_create("John", "Hansen")
    person_uuid = person_result.uuid

    await verify(person_uuid)

    sync_tool_mock.reset_mock()

    # Create an ITUser and verify that it ends up calling listen_to_changes_in_employees
    # In this case it does it by first emitting a employee_refresh event

    itsystem_result = await graphql_client.itsystem_create(
        ITSystemCreateInput(
            user_key="test", name="test", validity=RAOpenValidityInput()
        )
    )
    itsystem_uuid = itsystem_result.uuid

    await graphql_client._testing_ituser_create(
        ITUserCreateInput(
            person=person_uuid,
            user_key="test",
            itsystem=itsystem_uuid,
            validity=RAValidityInput(from_="1970-01-01T00:00:00"),
        )
    )

    await verify(person_uuid)