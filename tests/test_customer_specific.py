# SPDX-FileCopyrightText: Magenta ApS <https://magenta.dk>
# SPDX-License-Identifier: MPL-2.0
from datetime import datetime
from datetime import timedelta
from unittest.mock import AsyncMock
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from fastramqpi.context import Context
from pydantic import parse_obj_as

from mo_ldap_import_export.autogenerated_graphql_client.read_engagements_by_employee_uuid import (
    ReadEngagementsByEmployeeUuidEngagements,
)
from mo_ldap_import_export.models import JobTitleFromADToMO


@pytest.fixture
def context(
    dataloader: AsyncMock,
    converter: MagicMock,
    export_checks: AsyncMock,
    settings_mock: MagicMock,
) -> Context:
    context = Context(
        amqpsystem=AsyncMock(),
        user_context={
            "dataloader": dataloader,
            "converter": converter,
            "export_checks": export_checks,
            "settings": settings_mock,
        },
    )
    return context


async def test_import_jobtitlefromadtomo_objects() -> None:
    test_eng_uuid = uuid4()
    start_time = datetime.now() - timedelta(minutes=10)
    end_time = datetime.now()

    graphql_client_mock = AsyncMock()
    graphql_client_mock.read_engagements_by_employee_uuid.return_value = parse_obj_as(
        ReadEngagementsByEmployeeUuidEngagements,
        {
            "objects": [
                {
                    "current": {
                        "uuid": str(test_eng_uuid),
                        "validity": {"from": str(start_time), "to": str(end_time)},
                    }
                }
            ]
        },
    )
    test_user_uuid = uuid4()
    test_job_function_uuid = uuid4()
    test_object = JobTitleFromADToMO(
        user=test_user_uuid,
        job_function=test_job_function_uuid,
    )

    graphql_client_mock.set_job_title.assert_not_called()
    await test_object.sync_to_mo(graphql_client_mock)

    graphql_client_mock.set_job_title.assert_called_once_with(
        job_function=test_job_function_uuid,
        uuid=test_eng_uuid,
        **{"from_": start_time, "to": end_time},
    )
    graphql_client_mock.set_job_title.assert_awaited_once_with(
        job_function=test_job_function_uuid,
        uuid=test_eng_uuid,
        **{"from_": start_time, "to": end_time},
    )
