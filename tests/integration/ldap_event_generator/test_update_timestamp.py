# SPDX-FileCopyrightText: Magenta ApS <https://magenta.dk>
# SPDX-License-Identifier: MPL-2.0
from typing import cast
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastramqpi.context import Context
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.ext.asyncio import async_sessionmaker

from mo_ldap_import_export.config import Settings
from mo_ldap_import_export.ldap_event_generator import LastRun
from mo_ldap_import_export.ldap_event_generator import LDAPEventGenerator
from mo_ldap_import_export.types import LDAPUUID


async def get_last_run_cookie(
    sessionmaker: async_sessionmaker[AsyncSession], search_base: str
) -> bytes | None:
    async with sessionmaker() as session, session.begin():
        # Get last run cookie from database for updating
        last_run = await session.scalar(
            select(LastRun).where(LastRun.search_base == search_base)
        )
        if last_run is None:
            return None
        return last_run.cookie


async def num_last_run_entries(sessionmaker: async_sessionmaker[AsyncSession]) -> int:
    async with sessionmaker() as session, session.begin():
        result = await session.execute(select(LastRun))
        return len(result.fetchall())


@pytest.mark.integration_test
@pytest.mark.envvar(
    # If we are listening to changes in LDAP it will write concurrently with us
    {"LISTEN_TO_CHANGES_IN_LDAP": "False"}
)
@pytest.mark.usefixtures("test_client")
async def test_update_cookie_postgres(context: Context) -> None:
    sessionmaker = context["sessionmaker"]

    event_generator = LDAPEventGenerator(
        sessionmaker=sessionmaker,
        settings=Settings(),
        graphql_client=AsyncMock(),
        ldap_connection=AsyncMock(),
    )

    # Mock poll to return different cookies each time
    cookie_counter = [0]

    def mock_poll(*_, **__):
        cookie_counter[0] += 1
        cookie = f"cookie_{cookie_counter[0]}".encode()
        return ({cast(LDAPUUID, uuid4())}, cookie)

    event_generator.poll = AsyncMock(  # type: ignore[method-assign]
        side_effect=mock_poll
    )

    for count, search_base in enumerate(["dc=ad0", "dc=ad1", "dc=ad2"]):
        assert await num_last_run_entries(sessionmaker) == count

        last_run_cookie = await get_last_run_cookie(sessionmaker, search_base)
        assert last_run_cookie is None

        await event_generator._generate_events(search_base)
        first_cookie = await get_last_run_cookie(sessionmaker, search_base)
        assert first_cookie is not None
        assert await num_last_run_entries(sessionmaker) == count + 1

        await event_generator._generate_events(search_base)
        last_cookie = await get_last_run_cookie(sessionmaker, search_base)
        assert last_cookie is not None
        assert last_cookie != first_cookie  # Cookie should change
        assert await num_last_run_entries(sessionmaker) == count + 1


@pytest.mark.integration_test
@pytest.mark.envvar({"LISTEN_TO_CHANGES_IN_LDAP": "False"})
@pytest.mark.usefixtures("test_client")
async def test_update_cookie_no_changes(context: Context) -> None:
    sessionmaker = context["sessionmaker"]

    event_generator = LDAPEventGenerator(
        sessionmaker=sessionmaker,
        settings=Settings(),
        graphql_client=AsyncMock(),
        ldap_connection=AsyncMock(),
    )

    search_base = "dc=ad0"

    last_run_cookie = await get_last_run_cookie(sessionmaker, search_base)
    assert last_run_cookie is None

    # First call - should set initial cookie
    initial_cookie = b"initial_cookie"
    event_generator.poll = AsyncMock(  # type: ignore[method-assign]
        side_effect=lambda *_, **__: ({cast(LDAPUUID, uuid4())}, initial_cookie),
    )
    await event_generator._generate_events(search_base)
    first_cookie = await get_last_run_cookie(sessionmaker, search_base)
    assert first_cookie is not None
    assert first_cookie == initial_cookie

    # Second call with same cookie - should not change
    event_generator.poll = AsyncMock(  # type: ignore[method-assign]
        side_effect=lambda *_, **__: (set(), initial_cookie),
    )
    await event_generator._generate_events(search_base)
    last_cookie = await get_last_run_cookie(sessionmaker, search_base)
    assert last_cookie == first_cookie
