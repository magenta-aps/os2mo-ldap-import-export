# SPDX-FileCopyrightText: 2019-2020 Magenta ApS
# SPDX-License-Identifier: MPL-2.0
# -*- coding: utf-8 -*-
import asyncio
import datetime
import re
from unittest.mock import AsyncMock
from unittest.mock import MagicMock
from unittest.mock import patch
from uuid import uuid4

import pytest
import pytz  # type: ignore
from fastramqpi.ramqp.mo import MORoutingKey
from gql import gql
from graphql import print_ast
from ldap3.core.exceptions import LDAPInvalidDnError
from ramodels.mo.details.address import Address
from structlog.testing import capture_logs

from mo_ldap_import_export.exceptions import InvalidQuery
from mo_ldap_import_export.utils import add_filter_to_query
from mo_ldap_import_export.utils import combine_dn_strings
from mo_ldap_import_export.utils import datetime_to_ldap_timestamp
from mo_ldap_import_export.utils import delete_keys_from_dict
from mo_ldap_import_export.utils import exchange_ou_in_dn
from mo_ldap_import_export.utils import extract_cn_from_dn
from mo_ldap_import_export.utils import extract_ou_from_dn
from mo_ldap_import_export.utils import get_object_type_from_routing_key
from mo_ldap_import_export.utils import import_class
from mo_ldap_import_export.utils import listener
from mo_ldap_import_export.utils import mo_datestring_to_utc
from mo_ldap_import_export.utils import mo_object_is_valid
from mo_ldap_import_export.utils import remove_cn_from_dn
from mo_ldap_import_export.utils import remove_vowels


async def test_import_class():
    imported_class = import_class("ramodels.mo.employee.Employee")
    assert imported_class.__name__ == "Employee"

    imported_class = import_class("Custom.JobTitleFromADToMO")
    assert imported_class.__name__ == "JobTitleFromADToMO"


async def test_delete_keys_from_dict():
    dict_to_delete_from = {
        "foo": 1,
        "bar": 2,
        "nest": {"foo": 1, "bar": 2},
    }

    modified_dict = delete_keys_from_dict(dict_to_delete_from, ["foo"])

    assert "foo" in dict_to_delete_from
    assert "foo" in dict_to_delete_from["nest"]  # type:ignore
    assert "foo" not in modified_dict
    assert "foo" not in modified_dict["nest"]


async def test_add_filter_to_query():
    query1 = gql(
        """
        query TestQuery {
          employees {
            uuid
          }
        }
        """
    )

    query2 = gql(
        """
        query TestQuery {
          employees (uuid:"uuid") {
            uuid
          }
        }
        """
    )

    # A query without filters cannot be modified
    with pytest.raises(InvalidQuery):
        modified_query = add_filter_to_query(query1, "to_date: null, from_date: null")

    modified_query = add_filter_to_query(query2, "to_date: null, from_date: null")
    modified_query_str = print_ast(modified_query)
    assert "from_date" in modified_query_str
    assert "to_date" in modified_query_str


async def test_mo_datestring_to_utc():
    date = mo_datestring_to_utc("2023-02-27T00:00:00+01:00")
    assert date.strftime("%Y-%m-%d") == "2023-02-27"

    date = mo_datestring_to_utc("2023-02-27T00:00:00-03:00")
    assert date.strftime("%Y-%m-%d") == "2023-02-27"

    date = mo_datestring_to_utc("2023-02-27T01:02:03-03:00")
    assert date.strftime("%Y-%m-%d %H:%M:%S") == "2023-02-27 01:02:03"

    date = mo_datestring_to_utc(None)
    assert date is None


async def test_mo_object_is_valid():
    mo_object = Address.from_simplified_fields("foo", uuid4(), "2021-01-01")
    assert mo_object_is_valid(mo_object) is True

    mo_object = Address.from_simplified_fields(
        "foo", uuid4(), "2021-01-01", to_date="2200-01-01"
    )
    assert mo_object_is_valid(mo_object) is True

    mo_object = Address.from_simplified_fields(
        "foo", uuid4(), "2021-01-01", to_date="2021-02-01"
    )
    assert mo_object_is_valid(mo_object) is False


async def test_datetime_to_ldap_timestamp():
    date = datetime.datetime(2021, 1, 1, 10, 45, 20)
    result = datetime_to_ldap_timestamp(date)
    assert result == "20210101104520.0-0000"

    date = datetime.datetime(2021, 1, 1, 10, 45, 20, 2000)
    result = datetime_to_ldap_timestamp(date)
    assert result == "20210101104520.2-0000"

    date = datetime.datetime(2021, 1, 1, 10, 45, 20, 2100)
    result = datetime_to_ldap_timestamp(date)
    assert result == "20210101104520.2-0000"

    date = datetime.datetime(2021, 1, 1, 10, 45, 20, 2100, pytz.timezone("Cuba"))
    result = datetime_to_ldap_timestamp(date)
    assert result == "20210101104520.2-0529"


async def test_listener_callback():
    event_loop = asyncio.get_running_loop()

    async def publish_message(routing_key: str, dn: str):
        event_loop.call_soon(publish_message.event.set)  # type: ignore
        assert routing_key == "dn"
        assert dn == "CN=foo"
        raise ValueError("BOOM")

    publish_message.event = asyncio.Event()

    ldap_amqpsystem = AsyncMock()
    ldap_amqpsystem.publish_message = publish_message

    user_context = {"event_loop": event_loop, "ldap_amqpsystem": ldap_amqpsystem}
    context = {"user_context": user_context}

    event = {"attributes": {"distinguishedName": "CN=foo"}}

    with capture_logs() as cap_logs:
        listener(context, event)
        await publish_message.event.wait()

    assert len(cap_logs) == 2
    log = cap_logs[1]
    assert log["event"] == "Exception during listener"
    assert log["exc_info"] == "ValueError('BOOM')"


@patch("asyncio.run_coroutine_threadsafe")
async def test_listener(run_coroutine_threadsafe):
    callback = MagicMock()
    event_loop = MagicMock()
    ldap_amqpsystem = MagicMock()
    ldap_amqpsystem.publish_message.return_value = callback

    user_context = {"event_loop": event_loop, "ldap_amqpsystem": ldap_amqpsystem}

    context = {"user_context": user_context}

    event = {"attributes": {"distinguishedName": "CN=foo"}}
    with capture_logs() as cap_logs:
        listener(context, event)
        listener(context, {})

        messages = [w for w in cap_logs if w["log_level"] == "info"]
        assert re.match(
            "Registered change for LDAP object",
            str(messages[0]["event"]),
        )
        ldap_amqpsystem.publish_message.assert_called_with("dn", "CN=foo")
        run_coroutine_threadsafe.assert_called_with(callback, event_loop)

        assert re.match(
            "Got event without dn",
            str(messages[1]["event"]),
        )


def test_combine_dn_strings():
    assert combine_dn_strings(["CN=Nick", "", "DC=bar"]) == "CN=Nick,DC=bar"
    assert combine_dn_strings(["CN=Nick", "OU=f", "DC=bar"]) == "CN=Nick,OU=f,DC=bar"
    assert combine_dn_strings(["CN=Nick", "DC=bar"]) == "CN=Nick,DC=bar"


def test_remove_vowels():
    assert remove_vowels("food") == "fd"


def test_extract_ou_from_dn():
    assert extract_ou_from_dn("CN=Nick,OU=org,OU=main org,DC=f") == "OU=org,OU=main org"
    assert extract_ou_from_dn("CN=Nick,OU=org,DC=f") == "OU=org"
    assert extract_ou_from_dn("CN=Nick,DC=f") == ""

    with pytest.raises(LDAPInvalidDnError):
        extract_ou_from_dn("CN=Nick,OU=foo, DC=f")

    with pytest.raises(LDAPInvalidDnError):
        extract_ou_from_dn("")


def test_get_object_type_from_routing_key():
    routing_key: MORoutingKey = "address"
    assert get_object_type_from_routing_key(routing_key) == "address"


def test_remove_cn_from_dn():
    assert remove_cn_from_dn("CN=Nick,OU=foo,DC=bar") == "OU=foo,DC=bar"
    assert remove_cn_from_dn("CN=Nick,CN=Janssen,OU=foo,DC=bar") == "OU=foo,DC=bar"
    assert remove_cn_from_dn("OU=foo,DC=bar") == "OU=foo,DC=bar"
    assert remove_cn_from_dn("CN=Nick") == ""


def test_exchange_ou_in_dn():
    assert (
        exchange_ou_in_dn("CN=Tobias,OU=foo,DC=Q", "OU=bar") == "CN=Tobias,OU=bar,DC=Q"
    )
    assert (
        exchange_ou_in_dn("CN=Tobias,OU=foo,DC=Q", "OU=mucki,OU=bar")
        == "CN=Tobias,OU=mucki,OU=bar,DC=Q"
    )
    assert (
        exchange_ou_in_dn("CN=Tobias,OU=foo,OU=oof,DC=Q", "OU=mucki")
        == "CN=Tobias,OU=mucki,DC=Q"
    )
    assert (
        exchange_ou_in_dn("CN=Tobias,OU=foo,OU=oof", "OU=mucki,OU=bar")
        == "CN=Tobias,OU=mucki,OU=bar"
    )
    assert (
        exchange_ou_in_dn("CN=Tobias,OU=bar,DC=Q", "OU=bar") == "CN=Tobias,OU=bar,DC=Q"
    )


def test_extract_cn_from_dn():
    assert extract_cn_from_dn("CN=Nick,OU=foo,DC=bar") == "CN=Nick"
    assert (
        extract_cn_from_dn("CN=Nick,CN=Janssen,OU=foo,DC=bar") == "CN=Nick,CN=Janssen"
    )
