# SPDX-FileCopyrightText: 2019-2020 Magenta ApS
# SPDX-License-Identifier: MPL-2.0
import copy
import datetime
import os.path
import re
import uuid
from typing import Any
from unittest.mock import AsyncMock
from unittest.mock import MagicMock
from unittest.mock import patch
from uuid import UUID
from uuid import uuid4

import pytest
import yaml
from fastramqpi.context import Context
from fastramqpi.ramqp.utils import RequeueMessage
from jinja2 import Environment
from jinja2 import Undefined
from more_itertools import one
from pydantic import parse_obj_as
from pydantic import ValidationError
from ramodels.mo import Employee
from structlog.testing import capture_logs

from mo_ldap_import_export.config import check_attributes
from mo_ldap_import_export.config import ConversionMapping
from mo_ldap_import_export.config import LDAP2MOMapping
from mo_ldap_import_export.config import MO2LDAPMapping
from mo_ldap_import_export.converters import find_cpr_field
from mo_ldap_import_export.converters import find_ldap_it_system
from mo_ldap_import_export.converters import LdapConverter
from mo_ldap_import_export.customer_specific import JobTitleFromADToMO
from mo_ldap_import_export.dataloaders import LdapObject
from mo_ldap_import_export.environments import environment
from mo_ldap_import_export.exceptions import IncorrectMapping
from mo_ldap_import_export.exceptions import InvalidNameException
from mo_ldap_import_export.exceptions import NotSupportedException
from mo_ldap_import_export.exceptions import UUIDNotFoundException


@pytest.fixture
def address_type_uuid() -> str:
    return "f55abef6-5cb6-4c7e-9a62-ed4ab9371a72"


@pytest.fixture
def context(address_type_uuid: str) -> Context:
    mapping = {
        "ldap_to_mo": {
            "Employee": {
                "objectClass": "ramodels.mo.employee.Employee",
                "_import_to_mo_": "True",
                "givenname": "{{ldap.givenName}}",
                "surname": "{{ldap.sn}}",
                "cpr_no": "{{ldap.employeeID or None}}",
                "uuid": "{{ employee_uuid or NONE }}",
            },
            "Email": {
                "objectClass": "ramodels.mo.details.address.Address",
                "_import_to_mo_": "True",
                "value": "{{ldap.mail}}",
                "type": "{{'address'}}",
                "validity": (
                    "{{ dict(from_date = " "ldap.mail_validity_from|mo_datestring) }}"
                ),
                "address_type": (
                    "{{ dict(uuid=" "'f376deb8-4743-4ca6-a047-3241de8fe9d2') }}"
                ),
                "person": "{{ dict(uuid=employee_uuid or NONE) }}",
            },
            "Active Directory": {
                "objectClass": "ramodels.mo.details.it_system.ITUser",
                "_import_to_mo_": "True",
                "user_key": "{{ ldap.msSFU30Name or NONE }}",
                "itsystem": "{{ dict(uuid=get_it_system_uuid(ldap.itSystemName)) }}",
                "validity": "{{ dict(from_date=now()|mo_datestring) }}",
                "person": "{{ dict(uuid=employee_uuid or NONE) }}",
            },
        },
        "mo_to_ldap": {
            "Employee": {
                "objectClass": "user",
                "_export_to_ldap_": "True",
                "givenName": "{{mo_employee.givenname}}",
                "sn": "{{mo_employee.surname}}",
                "displayName": "{{mo_employee.surname}}, {{mo_employee.givenname}}",
                "name": "{{mo_employee.givenname}} {{mo_employee.surname}}",
                "employeeID": "{{mo_employee.cpr_no or None}}",
            },
            "Email": {
                "objectClass": "user",
                "_export_to_ldap_": "True",
                "employeeID": "{{mo_employee.cpr_no or None}}",
            },
            "Active Directory": {
                "objectClass": "user",
                "_export_to_ldap_": "True",
                "msSFU30Name": "{{mo_employee_it_user.user_key}}",
                "employeeID": "{{mo_employee.cpr_no}}",
            },
        },
    }

    settings_mock = MagicMock()
    settings_mock.ldap_search_base = "bar"
    settings_mock.default_org_unit_type = "Afdeling"
    settings_mock.default_org_unit_level = "N1"
    settings_mock.org_unit_path_string_separator = "\\"

    dataloader = AsyncMock()
    uuid1 = address_type_uuid
    uuid2 = str(uuid4())
    mo_employee_address_types = {
        uuid1: {"uuid": uuid1, "scope": "MAIL", "user_key": "Email"},
    }

    mo_org_unit_address_types = {
        uuid2: {"uuid": uuid2, "scope": "TEXT", "user_key": "Post"},
    }

    ad_uuid = str(uuid4())
    mo_it_systems = {
        ad_uuid: {"uuid": ad_uuid, "user_key": "Active Directory"},
    }

    dataloader.load_mo_employee_address_types.return_value = mo_employee_address_types
    dataloader.load_mo_org_unit_address_types.return_value = mo_org_unit_address_types
    dataloader.load_mo_it_systems.return_value = mo_it_systems
    dataloader.load_mo_org_units.return_value = {}

    dataloader.single_value = {
        "givenName": True,
        "sn": True,
        "displayName": True,
        "name": True,
        "dn": True,
        "employeeID": True,
        "postalAddress": False,
        "mail": True,
        "msSFU30Name": True,
        "itSystemName": True,
    }

    attribute_dict = {
        a: {"single_value": dataloader.single_value[a]}
        for a in dataloader.single_value.keys()
    }

    overview = {"user": {"attributes": attribute_dict}}

    dataloader.load_ldap_overview = MagicMock()
    dataloader.load_ldap_overview.return_value = overview
    org_unit_type_uuid = str(uuid4())
    org_unit_level_uuid = str(uuid4())
    dataloader.load_mo_org_unit_types.return_value = {
        org_unit_type_uuid: {"uuid": org_unit_type_uuid, "user_key": "Afdeling"}
    }
    dataloader.load_mo_org_unit_levels.return_value = {
        org_unit_level_uuid: {"uuid": org_unit_level_uuid, "user_key": "N1"}
    }

    context: Context = {
        "user_context": {
            "mapping": mapping,
            "settings": settings_mock,
            "dataloader": dataloader,
            "username_generator": MagicMock(),
            "event_loop": MagicMock(),
        }
    }

    return context


@pytest.fixture
async def converter(context: Context) -> LdapConverter:
    converter = LdapConverter(context)
    await converter._init()
    return converter


async def test_ldap_to_mo(converter: LdapConverter) -> None:
    employee_uuid = uuid4()
    result = await converter.from_ldap(
        LdapObject(
            dn="",
            name="",
            givenName="Tester",
            sn="Testersen",
            objectGUID="{" + str(uuid.uuid4()) + "}",
            employeeID="0101011234",
        ),
        "Employee",
        employee_uuid=employee_uuid,
    )
    employee = result[0]
    assert employee.givenname == "Tester"
    assert employee.surname == "Testersen"
    assert employee.uuid == employee_uuid

    result = await converter.from_ldap(
        LdapObject(
            dn="",
            mail="foo@bar.dk",
            mail_validity_from=datetime.datetime(2019, 1, 1, 0, 10, 0),
        ),
        "Email",
        employee_uuid=employee_uuid,
    )
    mail = result[0]

    assert mail.value == "foo@bar.dk"
    assert mail.person.uuid == employee_uuid
    from_date = mail.validity.dict()["from_date"].replace(tzinfo=None)

    # Note: Date is always at midnight in MO
    assert from_date == datetime.datetime(2019, 1, 1, 0, 0, 0)

    mail = await converter.from_ldap(
        LdapObject(
            dn="",
            mail=[],
            mail_validity_from=datetime.datetime(2019, 1, 1, 0, 10, 0),
        ),
        "Email",
        employee_uuid=employee_uuid,
    )

    assert not mail


async def test_ldap_to_mo_uuid_not_found(converter: LdapConverter) -> None:
    it_users_with_typo = await converter.from_ldap(
        LdapObject(
            dn="",
            msSFU30Name=["foo", "bar"],
            itSystemName=["Active Directory", "Active Directory_typo"],
        ),
        "Active Directory",
        employee_uuid=uuid4(),
    )

    it_users = await converter.from_ldap(
        LdapObject(
            dn="",
            msSFU30Name=["foo", "bar"],
            itSystemName=["Active Directory", "Active Directory"],
        ),
        "Active Directory",
        employee_uuid=uuid4(),
    )

    assert it_users[0].user_key == "foo"
    assert it_users[1].user_key == "bar"
    ad_uuid = converter.get_it_system_uuid("Active Directory")
    assert str(it_users[0].itsystem.uuid) == ad_uuid
    assert str(it_users[1].itsystem.uuid) == ad_uuid

    # Only one it user should be converted. The second one cannot be found because
    # "Active Directory_typo" does not exist as an it system in MO
    assert len(it_users_with_typo) == 1
    assert len(it_users) == 2


async def test_ldap_to_mo_dict_error(converter: LdapConverter) -> None:
    converter.mapping = converter._populate_mapping_with_templates(
        {
            "ldap_to_mo": {
                "Active Directory": {
                    "objectClass": "ramodels.mo.details.it_system.ITUser",
                    "user_key": "{{ ldap.msSFU30Name or NONE }}",
                    "itsystem": "{ 'hep': 'hey }",  # provokes json error in str_to_dict
                    "person": "{{ dict(uuid=employee_uuid or NONE) }}",
                }
            }
        },
        Environment(undefined=Undefined, enable_async=True),
    )

    with pytest.raises(IncorrectMapping):
        await converter.from_ldap(
            LdapObject(
                dn="",
                msSFU30Name=["foo", "bar"],
                itSystemName=["Active Directory", "Active Directory"],
            ),
            "Active Directory",
            employee_uuid=uuid4(),
        )


async def test_ldap_to_mo_dict_validation_error(converter: LdapConverter) -> None:
    converter.import_mo_object_class = MagicMock()  # type: ignore
    converter.import_mo_object_class.return_value = JobTitleFromADToMO

    converter.mapping = converter._populate_mapping_with_templates(
        {
            "ldap_to_mo": {
                "Custom": {
                    "objectClass": "Custom.JobTitleFromADToMO",
                    "_import_to_mo_": "true",
                    "user": "{{ dict(uuid=(ldap.hkStsuuid)) }}",
                    "job_function": f"{{ dict(uuid={uuid4()}) }}",
                    "job_function_fallback": f"{{ dict(uuid={uuid4()}) }}",
                    "uuid": "{{ employee_uuid or NONE }}",
                }
            }
        },
        Environment(undefined=Undefined, enable_async=True),
    )

    with capture_logs() as cap_logs:
        await converter.from_ldap(
            LdapObject(
                dn="",
                hkStsuuid="not_an_uuid",
                title="job title",
                comment="job title default",
            ),
            "Custom",
            employee_uuid=uuid4(),
        )

        info_messages = [w for w in cap_logs if w["log_level"] == "info"]
        assert "not a valid uuid" in str(info_messages)


async def test_ldap_to_mo_uses_engagement_uuid(converter: LdapConverter) -> None:
    """
    Passing an optional `engagement_uuid` to `from_ldap` should inject the engagement
    UUID in the returned MO object (if that object is an `Address` or `ITUser`.)
    """

    # Arrange
    employee_uuid: UUID = uuid4()
    engagement_uuid: UUID = uuid4()
    # Arrange: replace the 'render_async' method on one of the field templates with a
    # mock, so we can test the arguments passed to it.
    with patch.object(
        converter.mapping["ldap_to_mo"]["Email"]["value"],
        "render_async",
        return_value="<value>",
    ) as mock_render_async:
        # Act
        await converter.from_ldap(
            LdapObject(
                dn="",
                mail="foo@bar.dk",
                mail_validity_from=datetime.datetime(2019, 1, 1, 0, 10, 0),
            ),
            "Email",
            employee_uuid,
            engagement_uuid=engagement_uuid,
        )
        # Assert: check that `render_async` was awaited
        mock_render_async.assert_awaited_once()
        # Assert: check actual engagement UUID vs. the one passed to `from_ldap`
        context: dict = mock_render_async.await_args.args[0]  # type: ignore
        actual_engagement_uuid: UUID = UUID(context["engagement_uuid"])
        assert actual_engagement_uuid == engagement_uuid


async def test_mo_to_ldap(converter: LdapConverter) -> None:
    obj_dict: dict = {"mo_employee": Employee(givenname="Tester", surname="Testersen")}
    ldap_object: Any = await converter.to_ldap(obj_dict, "Employee", "CN=foo")
    assert ldap_object.givenName == "Tester"
    assert ldap_object.sn == "Testersen"
    assert ldap_object.name == "Tester Testersen"
    assert ldap_object.dn == "CN=foo"

    with pytest.raises(NotSupportedException):
        obj_dict = {"mo_employee_address": "foo"}
        await converter.to_ldap(obj_dict, "Employee", "CN=foo")


async def test_mapping_loader() -> None:
    file_path = os.path.join(os.path.dirname(__file__), "resources", "mapping.yaml")
    with open(file_path) as file:
        mapping = yaml.safe_load(file)
    expected = {
        "ldap_to_mo": {
            "Employee": {
                "objectClass": "ramodels.mo.employee.Employee",
                "_import_to_mo_": "true",
                "givenname": "{{ldap.givenName or ldap.name|splitlast|first}}",
                "surname": "{{ldap.surname or ldap.sn or "
                "ldap.name|splitlast|last or ''}}",
                "cpr_no": "{{ldap.cpr or None}}",
                "seniority": "{{ldap.seniority or None}}",
                "nickname_givenname": "{{ldap.nickname_givenname or None}}",
                "nickname_surname": "{{ldap.nickname_surname or None}}",
            }
        },
        "mo_to_ldap": {
            "Employee": {
                "objectClass": "user",
                "_export_to_ldap_": "true",
                "givenName": "{{mo_employee.givenname}}",
                "sn": "{{mo_employee.surname}}",
                "displayName": "{{mo_employee.surname}}, {{mo_employee.givenname}}",
                "name": "{{mo_employee.givenname}} {{mo_employee.surname}}",
                "cpr": "{{mo_employee.cpr_no or None}}",
                "seniority": "{{mo_employee.seniority or None}}",
                "nickname_givenname": "{{mo_employee.nickname_givenname or None}}",
                "nickname_surname": "{{mo_employee.nickname_surname or None}}",
            }
        },
    }
    assert mapping == expected


async def test_mapping_loader_failure(context: Context) -> None:
    good_context = copy.deepcopy(context)

    for bad_mapping in ({}, {"ldap_to_mo": {}}, {"mo_to_ldap": {}}):
        bad_context = copy.deepcopy(context)
        bad_context["user_context"]["mapping"] = bad_mapping

        with pytest.raises(IncorrectMapping):
            await LdapConverter(context=bad_context)._init()
        with pytest.raises(IncorrectMapping):
            await LdapConverter(context=bad_context)._init()

        converter = LdapConverter(context=good_context)
        await converter._init()
        converter.mapping = bad_mapping
        with pytest.raises(IncorrectMapping):
            await converter.from_ldap(
                LdapObject(
                    dn="",
                    name="",
                    givenName="Tester",
                    sn="Testersen",
                    objectGUID="{" + str(uuid.uuid4()) + "}",
                    employeeID="0101011234",
                ),
                "Employee",
                employee_uuid=uuid4(),
            )
        with pytest.raises(IncorrectMapping):
            obj_dict = {
                "mo_employee": Employee(givenname="Tester", surname="Testersen")
            }
            await converter.to_ldap(obj_dict, "Employee", "CN=foo")


async def test_find_cpr_field(converter: LdapConverter) -> None:
    # This mapping is accepted
    good_mapping = {
        "mo_to_ldap": {
            "Employee": {
                "objectClass": "user",
                "_export_to_ldap_": "True",
                "employeeID": "{{mo_employee.cpr_no or None}}",
            }
        },
        "ldap_to_mo": {
            "Employee": {
                "objectClass": "ramodels.mo.employee.Employee",
                "_import_to_mo_": "True",
                "uuid": "{{ employee_uuid }}",
            }
        },
    }

    # This mapping does not contain the mo_employee.cpr_no field
    bad_mapping = {
        "mo_to_ldap": {
            "Employee": {
                "objectClass": "user",
                "_export_to_ldap_": "True",
                "givenName": "{{mo_employee.givenname}}",
            }
        },
        "ldap_to_mo": {
            "Employee": {
                "objectClass": "ramodels.mo.employee.Employee",
                "_import_to_mo_": "True",
                "uuid": "{{ employee_uuid }}",
            }
        },
    }

    # Test both cases
    populated_good_mapping = converter._populate_mapping_with_templates(
        good_mapping, environment
    )
    populated_bad_mapping = converter._populate_mapping_with_templates(
        bad_mapping, environment
    )
    populated_incorrect_mapping = converter._populate_mapping_with_templates(
        {"mo_to_ldap": {}}, environment
    )

    assert await find_cpr_field(populated_good_mapping) == "employeeID"
    assert await find_cpr_field(populated_bad_mapping) is None

    with pytest.raises(IncorrectMapping):
        await find_cpr_field(populated_incorrect_mapping)


async def test_find_cpr_field_jinja_compile_fail(converter: LdapConverter) -> None:
    mapping = {
        "mo_to_ldap": {
            "Employee": {
                "objectClass": "user",
                "_export_to_ldap_": "True",
                # Some internal jinja thing going on here. It does not crash
                # if it is not an expression like `+ " "` 🤷
                "shouldFail": '{{ mo_employee.crash + " " }}',
                "employeeID": "{{mo_employee.cpr_no or None}}",
            }
        },
    }
    mapping = converter._populate_mapping_with_templates(mapping, environment)
    assert await find_cpr_field(mapping) == "employeeID"


async def test_template_lenience(context: Context, converter: LdapConverter) -> None:
    mapping = {
        "ldap_to_mo": {
            "Employee": {
                "objectClass": "ramodels.mo.employee.Employee",
                "_import_to_mo_": "True",
                "givenname": "{{ldap.givenName}}",
                "surname": "{{ldap.sn}}",
                "uuid": "{{ employee_uuid }}",
            }
        },
        "mo_to_ldap": {
            "Employee": {
                "objectClass": "user",
                "_export_to_ldap_": "True",
                "givenName": "{{mo_employee.givenname}}",
                "sn": "{{mo_employee.surname}}",
                "displayName": "{{mo_employee.surname}}, {{mo_employee.givenname}}",
                "name": "{{mo_employee.givenname}} {{mo_employee.surname}}",
                "dn": "",
                "employeeID": "{{mo_employee.cpr_no or None}}",
            }
        },
    }

    context["user_context"]["mapping"] = mapping
    await converter.from_ldap(
        LdapObject(
            dn="",
            cpr="1234567890",
        ),
        "Employee",
        employee_uuid=uuid4(),
    )


def test_find_object_class(converter: LdapConverter):
    output = converter.find_object_class("Employee", "ldap_to_mo")
    assert output == "ramodels.mo.employee.Employee"

    output = converter.find_object_class("Employee", "mo_to_ldap")
    assert output == "user"

    with pytest.raises(IncorrectMapping):
        converter.find_object_class("non_existing_json_key", "mo_to_ldap")


def test_find_ldap_object_class(converter: LdapConverter):
    object_class = converter.find_ldap_object_class("Employee")
    assert object_class == "user"


def test_find_mo_object_class(converter: LdapConverter):
    object_class = converter.find_mo_object_class("Employee")
    assert object_class == "ramodels.mo.employee.Employee"


def test_get_ldap_attributes(converter: LdapConverter, context: Context) -> None:
    attributes = set(converter.get_ldap_attributes("Employee"))
    all_attributes = set(
        context["user_context"]["mapping"]["mo_to_ldap"]["Employee"].keys()
    )
    assert all_attributes - attributes == {"objectClass", "_export_to_ldap_"}


async def test_get_ldap_attributes_dn_removed(context: Context) -> None:
    context["user_context"]["mapping"]["mo_to_ldap"]["Employee"]["dn"] = "fixed"

    converter = LdapConverter(context)
    await converter._init()

    attributes = set(converter.get_ldap_attributes("Employee"))
    all_attributes = set(
        context["user_context"]["mapping"]["mo_to_ldap"]["Employee"].keys()
    )
    assert all_attributes - attributes == {"objectClass", "_export_to_ldap_", "dn"}


def test_get_mo_attributes(converter: LdapConverter) -> None:
    attributes = set(converter.get_mo_attributes("Employee"))
    assert attributes == {"uuid", "cpr_no", "surname", "givenname"}


def test_check_converter_attributes(converter: LdapConverter):
    detected_attributes = ["foo", "bar"]
    accepted_attributes = ["bar"]

    with pytest.raises(IncorrectMapping):
        converter.check_attributes(detected_attributes, accepted_attributes)

    detected_attributes = ["bar", "extensionAttribute14", "sAMAccountName"]
    accepted_attributes = ["bar"]
    converter.check_attributes(detected_attributes, accepted_attributes)


def test_check_attributes():
    detected_attributes = {"foo", "bar"}
    accepted_attributes = {"bar"}

    with pytest.raises(ValueError):
        check_attributes(detected_attributes, accepted_attributes)

    detected_attributes = {"bar", "extensionAttribute14", "sAMAccountName"}
    accepted_attributes = {"bar"}
    check_attributes(detected_attributes, accepted_attributes)


def test_get_accepted_json_keys(converter: LdapConverter):
    output = converter.get_accepted_json_keys()
    assert len(output) == 6
    assert "Employee" in output
    assert "Engagement" in output
    assert "Custom" in output
    assert "Email" in output
    assert "Post" in output
    assert "Active Directory" in output


def test_min(converter: LdapConverter):
    assert converter.min(1, None) == 1
    assert converter.min(None, 1) == 1
    assert converter.min(9, 10) == 9
    assert converter.min(10, 9) == 9


def test_nonejoin(converter: LdapConverter):
    output = converter.nonejoin("foo", "bar", None)
    assert output == "foo, bar"


def test_nonejoin_orgs(converter: LdapConverter):
    converter.org_unit_path_string_separator = "|"
    output = converter.nonejoin_orgs("", "org1 ", " org2", None, "")
    assert output == "org1|org2"


def test_str_to_dict(converter: LdapConverter):
    output = converter.str_to_dict("{'foo':2}")
    assert output == {"foo": 2}

    output = converter.str_to_dict("{'foo':Undefined}")
    assert output == {"foo": None}


def test_get_number_of_entries(converter: LdapConverter):
    single_entry_object = LdapObject(dn="foo", value="bar")
    multi_entry_object = LdapObject(dn="foo", value=["bar", "bar2"])

    output = converter.get_number_of_entries(single_entry_object)
    assert output == 1

    output = converter.get_number_of_entries(multi_entry_object)
    assert output == 2


EMPLOYEE_OBJ = {
    "objectClass": "ramodels.mo.employee.Employee",
    "uuid": "{{ employee_uuid }}",
}
MO_OBJ = {**EMPLOYEE_OBJ, "_export_to_ldap_": "true"}
LDAP_OBJ = {**EMPLOYEE_OBJ, "_import_to_mo_": "true"}


@pytest.mark.parametrize(
    "overlay,expected",
    [
        (
            {
                "ldap_to_mo": {"foo": LDAP_OBJ, "bar": LDAP_OBJ},
                "mo_to_ldap": {"bar": MO_OBJ},
            },
            "Missing keys in 'mo_to_ldap'",
        ),
        (
            {
                "ldap_to_mo": {"foo": LDAP_OBJ},
                "mo_to_ldap": {"foo": MO_OBJ, "bar": MO_OBJ},
            },
            "Missing keys in 'ldap_to_mo'",
        ),
    ],
)
async def test_cross_check_keys(
    converter: LdapConverter,
    overlay: dict[str, Any],
    expected: str,
) -> None:
    converter.raw_mapping["username_generator"] = {}

    converter.raw_mapping.update(overlay)
    with pytest.raises(ValidationError, match=expected):
        parse_obj_as(ConversionMapping, converter.raw_mapping)


async def test_check_key_validity(converter: LdapConverter):
    with patch(
        "mo_ldap_import_export.converters.LdapConverter.get_mo_to_ldap_json_keys",
        return_value=["foo", "bar"],
    ), patch(
        "mo_ldap_import_export.converters.LdapConverter.get_ldap_to_mo_json_keys",
        return_value=["foo", "bar"],
    ), patch(
        "mo_ldap_import_export.converters.LdapConverter.get_accepted_json_keys",
        return_value=["foo"],
    ):
        with pytest.raises(
            IncorrectMapping,
            match="{'bar'} are not valid keys. Accepted keys are {'foo'}",
        ):
            converter.check_key_validity()


async def test_check_for_objectClass(converter: LdapConverter):
    with pytest.raises(ValidationError, match="objectClass\n  field required"):
        parse_obj_as(MO2LDAPMapping, {"foo": {}})

    converter.raw_mapping = {
        "ldap_to_mo": {"foo": {"objectClass": "ramodels.mo.employee.Employee"}},
        "mo_to_ldap": {"foo": {}},
    }
    with pytest.raises(ValidationError, match="objectClass\n  field required"):
        parse_obj_as(ConversionMapping, converter.raw_mapping)


async def test_check_for_primary_specialcase():
    with pytest.raises(
        ValidationError, match="Missing {'primary'} which are mandatory."
    ):
        parse_obj_as(
            LDAP2MOMapping,
            {
                "objectClass": "ramodels.mo.details.engagement.Engagement",
                "org_unit": "val",
                "job_function": "val",
                "user_key": "val",
                "engagement_type": "val",
                "person": "val",
                "validity": "val",
            },
        )


async def test_check_ldap_attributes_single_value_fields(converter: LdapConverter):
    dataloader = MagicMock()
    dataloader.load_ldap_overview.return_value = {
        "user": {"attributes": ["attr1", "attr2", "attr3", "attr4"]}
    }

    mapping = {
        "mo_to_ldap": {
            "Address": {
                "attr1": "{{ mo_employee_address.value }}",
                "cpr_field": "{{ foo }}",
            },
            "AD": {
                "attr1": "{{ mo_employee_it_user.user_key }}",
                "cpr_field": "{{ foo }}",
            },
            "Engagement": {
                "attr1": "{{ mo_employee_engagement.user_key }}",
                "attr2": "{{ mo_employee_engagement.org_unit.uuid }}",
                "attr3": "{{ mo_employee_engagement.engagement_type.uuid }}",
                "attr4": "{{ mo_employee_engagement.job_function.uuid }}",
                "cpr_field": "{{ foo }}",
            },
        },
        "ldap_to_mo": {
            "Address": {"value": "ldap.value"},
            "AD": {"user_key": "ldap.user_key"},
            "Engagement": {
                "user_key": "ldap.user_key",
                "org_unit": "ldap.org_unit",
                "engagement_type": "ldap.engagement_type",
                "job_function": "ldap.job_function",
            },
        },
    }
    converter.raw_mapping = mapping.copy()
    converter.mapping = mapping.copy()
    converter.mo_address_types = ["Address"]
    converter.mo_it_systems = ["AD"]

    with patch(
        "mo_ldap_import_export.converters.find_cpr_field",
        return_value="cpr_field",
    ), patch(
        "mo_ldap_import_export.converters.LdapConverter.check_attributes",
        return_value=None,
    ), patch(
        "mo_ldap_import_export.converters.LdapConverter.find_ldap_object_class",
        return_value="user",
    ):
        with capture_logs() as cap_logs:
            dataloader.single_value = {
                "attr1": True,
                "cpr_field": False,
                "attr2": True,
                "attr3": True,
                "attr4": True,
            }
            converter.dataloader = dataloader

            converter.check_ldap_attributes()

            warnings = [w for w in cap_logs if w["log_level"] == "warning"]
            assert len(warnings) == 6
            for warning in warnings:
                assert re.match(
                    ".*LDAP attribute cannot contain multiple values.*",
                    warning["event"],
                )
        with pytest.raises(IncorrectMapping, match="LDAP Attributes .* are a mix"):
            dataloader.single_value = {
                "attr1": True,
                "cpr_field": False,
                "attr2": True,
                "attr3": True,
                "attr4": False,
            }
            converter.dataloader = dataloader

            converter.check_ldap_attributes()

        with pytest.raises(IncorrectMapping, match="Could not find all attributes"):
            mapping = {
                "mo_to_ldap": {
                    "Engagement": {
                        "attr1": "{{ mo_employee_engagement.user_key }}",
                        "attr2": "{{ mo_employee_engagement.org_unit.uuid }}",
                        "attr3": "{{ mo_employee_engagement.engagement_type.uuid }}",
                        "cpr_field": "{{ foo }}",
                    },
                },
                "ldap_to_mo": {
                    "Engagement": {
                        "user_key": "ldap.user_key",
                        "org_unit": "ldap.org_unit",
                        "engagement_type": "ldap.engagement_type",
                        "job_function": "ldap.job_function",
                    },
                },
            }
            converter.raw_mapping = mapping.copy()
            converter.mapping = mapping.copy()
            converter.check_ldap_attributes()


async def test_check_ldap_attributes_engagement_requires_single_value_fields(
    converter: LdapConverter,
) -> None:
    """
    Verify that `LdapConverter.check_ldap_attributes` checks that all AD fields mapped
    to a MO Engagement are indeed single-value fields. Otherwise, an `IncorrectMapping`
    exception is raised.
    """
    # Much of this is copied from `test_check_ldap_attributes_single_value_fields`.

    # Arrange
    dataloader = MagicMock()
    dataloader.load_ldap_overview.return_value = {
        "user": {"attributes": ["attr1", "attr2", "attr3", "attr4"]}
    }
    mapping = {
        "mo_to_ldap": {
            "Engagement": {
                "attr1": "{{ mo_employee_engagement.user_key }}",
                "attr2": "{{ mo_employee_engagement.org_unit.uuid }}",
                "attr3": "{{ mo_employee_engagement.engagement_type.uuid }}",
                "attr4": "{{ mo_employee_engagement.job_function.uuid }}",
                "cpr_field": "{{ foo }}",
            },
        },
        "ldap_to_mo": {
            "Engagement": {
                "user_key": "ldap.user_key",
                "org_unit": "ldap.org_unit",
                "engagement_type": "ldap.engagement_type",
                "job_function": "ldap.job_function",
            },
        },
    }
    converter.raw_mapping = mapping.copy()
    converter.mapping = mapping.copy()

    with patch(
        "mo_ldap_import_export.converters.find_cpr_field",
        return_value="cpr_field",
    ), patch(
        "mo_ldap_import_export.converters.LdapConverter.check_attributes",
        return_value=None,
    ), patch(
        "mo_ldap_import_export.converters.LdapConverter.find_ldap_object_class",
        return_value="user",
    ):
        # Assert
        with pytest.raises(
            IncorrectMapping,
            match="LDAP Attributes mapping to 'Engagement' contain one or more "
            "multi-value attributes .*, which is not allowed",
        ):
            dataloader.single_value = {
                # *All* mapped AD fields must be multi-value to reach the relevant
                # check.
                "attr1": False,
                "attr2": False,
                "attr3": False,
                "attr4": False,
                "cpr_field": False,
            }
            converter.dataloader = dataloader
            # Act
            converter.check_ldap_attributes()


async def test_check_ldap_attributes_fields_to_check(converter: LdapConverter):
    dataloader = MagicMock()
    dataloader.load_ldap_overview.return_value = {
        "user": {"attributes": ["attr1", "attr2", "attr3", "attr4"]}
    }

    with patch(
        "mo_ldap_import_export.converters.find_cpr_field",
        return_value="cpr_field",
    ), patch(
        "mo_ldap_import_export.converters.LdapConverter.check_attributes",
        return_value=None,
    ), patch(
        "mo_ldap_import_export.converters.LdapConverter.find_ldap_object_class",
        return_value="user",
    ):
        dataloader.single_value = {
            "attr1": True,
            "cpr_field": True,
            "attr2": True,
            "attr3": True,
            "attr4": True,
        }
        converter.dataloader = dataloader

        # This mapping is not allowed - because mo_employee_engagement.org_unit.uuid is
        # not in the mo_to_ldap templates
        with pytest.raises(IncorrectMapping):
            mapping = {
                "ldap_to_mo": {
                    "Engagement": {
                        "user_key": "ldap.user_key",
                        "org_unit": "ldap.org_unit",
                        "engagement_type": "ldap.engagement_type",
                        "job_function": "ldap.job_function",
                    },
                },
                "mo_to_ldap": {
                    "Engagement": {
                        "attr1": "{{ mo_employee_engagement.user_key }}",
                        # "attr2": "{{ mo_employee_engagement.org_unit.uuid }}",
                        "attr3": "{{ mo_employee_engagement.engagement_type.uuid }}",
                        "attr4": "{{ mo_employee_engagement.job_function.uuid }}",
                        "cpr_field": "{{ foo }}",
                    },
                },
            }
            converter.raw_mapping = mapping.copy()
            converter.mapping = mapping.copy()
            converter.check_ldap_attributes()

        # This mapping is OK. mo_employee_engagement.org_unit.uuid no longer needs to be
        # in the mo_to_ldap templates. Because the value does not come from LDAP in the
        # first place.
        mapping = {
            "ldap_to_mo": {
                "Engagement": {
                    "user_key": "ldap.user_key",
                    "org_unit": "fixed org unit",
                    "engagement_type": "ldap.engagement_type",
                    "job_function": "ldap.job_function",
                },
            },
            "mo_to_ldap": {
                "Engagement": {
                    "attr1": "{{ mo_employee_engagement.user_key }}",
                    # "attr2": "{{ mo_employee_engagement.org_unit.uuid }}",
                    "attr3": "{{ mo_employee_engagement.engagement_type.uuid }}",
                    "attr4": "{{ mo_employee_engagement.job_function.uuid }}",
                    "cpr_field": "{{ foo }}",
                },
            },
        }
        converter.raw_mapping = mapping.copy()
        converter.mapping = mapping.copy()
        converter.check_ldap_attributes()


async def test_check_dar_scope(converter: LdapConverter):
    uuid1 = str(uuid4())
    uuid2 = str(uuid4())
    employee_address_type_info = {
        uuid1: {"scope": "TEXT", "user_key": "foo", "uuid": uuid1},
    }
    org_unit_address_type_info = {
        uuid2: {"scope": "DAR", "user_key": "bar", "uuid": uuid2},
    }
    converter.employee_address_type_info = employee_address_type_info
    converter.org_unit_address_type_info = org_unit_address_type_info

    with patch(
        "mo_ldap_import_export.converters.LdapConverter.get_ldap_to_mo_json_keys",
        return_value=["foo", "bar"],
    ), patch(
        "mo_ldap_import_export.converters.LdapConverter.find_mo_object_class",
        return_value="ramodels.mo.details.address.Address",
    ):
        with pytest.raises(
            IncorrectMapping,
            match="maps to an address with scope = 'DAR'",
        ):
            converter.check_dar_scope()


async def test_get_address_type_uuid(converter: LdapConverter):
    uuid1 = str(uuid4())
    uuid2 = str(uuid4())

    employee_address_type_info = {
        uuid1: {"uuid": uuid1, "user_key": "foo"},
        uuid2: {"uuid": uuid2, "user_key": "bar"},
    }
    converter.employee_address_type_info = employee_address_type_info

    org_unit_address_type_info = {
        uuid1: {"uuid": uuid1, "user_key": "foo-org"},
        uuid2: {"uuid": uuid2, "user_key": "bar-org"},
    }
    converter.org_unit_address_type_info = org_unit_address_type_info

    assert converter.get_employee_address_type_uuid("foo") == uuid1
    assert converter.get_employee_address_type_uuid("bar") == uuid2
    assert converter.get_org_unit_address_type_uuid("foo-org") == uuid1
    assert converter.get_org_unit_address_type_uuid("bar-org") == uuid2


async def test_get_it_system_uuid(converter: LdapConverter):
    uuid1 = str(uuid4())
    uuid2 = str(uuid4())
    it_system_info = {
        uuid1: {"uuid": uuid1, "user_key": "AD"},
        uuid2: {"uuid": uuid2, "user_key": "Office365"},
    }
    converter.it_system_info = it_system_info

    assert converter.get_it_system_uuid("AD") == uuid1
    assert converter.get_it_system_uuid("Office365") == uuid2


async def test_get_visibility_uuid(converter: LdapConverter):
    uuid1 = str(uuid4())
    uuid2 = str(uuid4())
    visibility_info = {
        uuid1: {"uuid": uuid1, "user_key": "Hemmelig"},
        uuid2: {"uuid": uuid2, "user_key": "Offentlig"},
    }
    converter.visibility_info = visibility_info

    assert converter.get_visibility_uuid("Hemmelig") == uuid1
    assert converter.get_visibility_uuid("Offentlig") == uuid2


async def test_get_job_function_uuid(converter: LdapConverter):
    uuid1 = str(uuid4())
    uuid2 = str(uuid4())
    job_function_info = {
        uuid1: {"uuid": uuid1, "name": "Major"},
        uuid2: {"uuid": uuid2, "name": "Secretary"},
    }
    converter.job_function_info = job_function_info

    assert await converter.get_or_create_job_function_uuid("Major") == uuid1
    assert await converter.get_or_create_job_function_uuid("Secretary") == uuid2

    uuid = uuid4()

    dataloader = AsyncMock()
    dataloader.create_mo_job_function.return_value = uuid
    converter.dataloader = dataloader

    assert await converter.get_or_create_job_function_uuid("non-existing_job") == str(
        uuid
    )

    with pytest.raises(UUIDNotFoundException):
        await converter.get_or_create_job_function_uuid("")

    with pytest.raises(UUIDNotFoundException):
        await converter.get_or_create_job_function_uuid([])  # type: ignore


async def test_get_job_function_uuid_default_kwarg(converter: LdapConverter) -> None:
    """Test that a provided `default` is used if the value of `job_function` is falsy."""
    # Arrange: mock the UUID of a newly created job function
    uuid_for_new_job_function = str(uuid4())
    dataloader = AsyncMock()
    dataloader.create_mo_job_function.return_value = uuid_for_new_job_function
    converter.dataloader = dataloader
    converter.job_function_info = {
        uuid_for_new_job_function: {"uuid": uuid_for_new_job_function, "name": "Name"}
    }
    # Act
    result = await converter.get_or_create_job_function_uuid("", default="Default")
    # Assert
    assert result == uuid_for_new_job_function


async def test_get_job_function_uuid_default_kwarg_does_not_override(
    converter: LdapConverter,
) -> None:
    """Test that a provided `default` is *not* used if the value of `job_function` is
    truthy."""
    # Arrange
    uuid = str(uuid4())
    dataloader = AsyncMock()
    dataloader.create_mo_job_function.return_value = uuid
    converter.dataloader = dataloader
    converter.job_function_info = {uuid: {"uuid": uuid, "name": "Something"}}
    # Act
    result = await converter.get_or_create_job_function_uuid(
        "Something", default="Default"
    )
    # Assert
    assert result == uuid


async def test_get_org_unit_name(converter: LdapConverter) -> None:
    org_unit_uuid: str = str(uuid4())
    converter.org_unit_info = {org_unit_uuid: {"name": "Name"}}
    name = await converter.get_org_unit_name(org_unit_uuid)
    assert name == "Name"


async def test_get_engagement_type_uuid(converter: LdapConverter):
    uuid1 = str(uuid4())
    uuid2 = str(uuid4())
    engagement_type_info = {
        uuid1: {"uuid": uuid1, "name": "Ansat"},
        uuid2: {"uuid": uuid2, "name": "Vikar"},
    }
    converter.engagement_type_info = engagement_type_info

    assert await converter.get_or_create_engagement_type_uuid("Ansat") == uuid1
    assert await converter.get_or_create_engagement_type_uuid("Vikar") == uuid2

    uuid = uuid4()

    dataloader = AsyncMock()
    dataloader.create_mo_engagement_type.return_value = uuid
    converter.dataloader = dataloader

    assert await converter.get_or_create_engagement_type_uuid(
        "non-existing_engagement_type"
    ) == str(uuid)

    with pytest.raises(UUIDNotFoundException):
        await converter.get_or_create_engagement_type_uuid("")

    with pytest.raises(UUIDNotFoundException):
        await converter.get_or_create_engagement_type_uuid([])  # type: ignore


async def test_get_primary_type_uuid(converter: LdapConverter):
    uuid1 = str(uuid4())
    uuid2 = str(uuid4())
    primary_type_info = {
        uuid1: {"uuid": uuid1, "user_key": "primary"},
        uuid2: {"uuid": uuid2, "user_key": "non-primary"},
    }
    converter.primary_type_info = primary_type_info

    assert converter.get_primary_type_uuid("primary") == uuid1
    assert converter.get_primary_type_uuid("non-primary") == uuid2


async def test_get_it_system_user_key(converter: LdapConverter):
    uuid1 = str(uuid4())
    uuid2 = str(uuid4())
    it_system_info = {
        uuid1: {"uuid": uuid1, "user_key": "AD"},
        uuid2: {"uuid": uuid2, "user_key": "Office365"},
    }
    converter.it_system_info = it_system_info

    assert await converter.get_it_system_user_key(uuid1) == "AD"
    assert await converter.get_it_system_user_key(uuid2) == "Office365"


async def test_get_address_type_user_key(converter: LdapConverter):
    uuid1 = str(uuid4())
    uuid2 = str(uuid4())

    employee_address_type_info = {
        uuid2: {"uuid": uuid2, "user_key": "EmailEmployee"},
    }

    org_unit_address_type_info = {
        uuid1: {"uuid": uuid1, "user_key": "EmailUnit"},
    }

    converter.org_unit_address_type_info = org_unit_address_type_info
    converter.employee_address_type_info = employee_address_type_info

    assert await converter.get_employee_address_type_user_key(uuid2) == "EmailEmployee"
    assert await converter.get_org_unit_address_type_user_key(uuid1) == "EmailUnit"


async def test_get_engagement_type_name(converter: LdapConverter):
    uuid1 = str(uuid4())
    uuid2 = str(uuid4())
    engagement_type_info = {
        uuid1: {"uuid": uuid1, "name": "Ansat"},
        uuid2: {"uuid": uuid2, "name": "Vikar"},
    }
    converter.engagement_type_info = engagement_type_info

    assert await converter.get_engagement_type_name(uuid1) == "Ansat"
    assert await converter.get_engagement_type_name(uuid2) == "Vikar"


async def test_get_job_function_name(converter: LdapConverter):
    uuid1 = str(uuid4())
    uuid2 = str(uuid4())
    job_function_info = {
        uuid1: {"uuid": uuid1, "name": "Major"},
        uuid2: {"uuid": uuid2, "name": "Secretary"},
    }
    converter.job_function_info = job_function_info

    assert await converter.get_job_function_name(uuid1) == "Major"
    assert await converter.get_job_function_name(uuid2) == "Secretary"


async def test_check_ldap_to_mo_references(converter: LdapConverter):
    converter.raw_mapping = {
        "ldap_to_mo": {
            "Employee": {"active": True, "name": "{{ ldap.nonExistingAttribute}}"}
        }
    }

    with patch(
        "mo_ldap_import_export.converters.LdapConverter.get_ldap_to_mo_json_keys",
        return_value=["Employee"],
    ), patch(
        "mo_ldap_import_export.converters.LdapConverter.find_ldap_object_class",
        return_value="user",
    ):
        with pytest.raises(
            IncorrectMapping,
            match="Attribute 'nonExistingAttribute' not allowed",
        ):
            converter.check_ldap_to_mo_references()


def test_get_object_uuid_from_user_key(converter: LdapConverter):
    uuid = str(uuid4())
    name = "Skt. Joseph Skole"
    info_dict = {uuid: {"uuid": uuid, "user_key": name}}
    assert converter.get_object_uuid_from_user_key(info_dict, name) == uuid

    with pytest.raises(UUIDNotFoundException):
        info_dict = {uuid: {"uuid": uuid, "user_key": name}}
        converter.get_object_uuid_from_user_key(info_dict, "bar")

    with pytest.raises(UUIDNotFoundException):
        converter.get_object_uuid_from_user_key(info_dict, "")

    uuid2 = str(uuid4())
    # Check that a perfect match will be preferred over a normalized match
    info_dict = {
        uuid2: {"uuid": uuid2, "user_key": name.lower()},
        uuid: {"uuid": uuid, "user_key": name},
    }
    assert converter.get_object_uuid_from_user_key(info_dict, name) == uuid

    # Check that if no perfect matches exist, use the first match
    info_dict = {
        uuid: {"uuid": uuid, "user_key": name.upper()},
        uuid2: {"uuid": uuid2, "user_key": name.lower()},
    }
    assert converter.get_object_uuid_from_user_key(info_dict, name) == uuid


async def test_create_org_unit(converter: LdapConverter):
    uuids = [str(uuid4()), str(uuid4()), str(uuid4())]
    org_units = ["Magenta Aps", "Magenta Aarhus", "GrønlandsTeam"]
    org_unit_infos = [
        {"name": org_units[i], "uuid": uuids[i]} for i in range(len(uuids))
    ]

    uuid_root_org_uuid = uuid4()
    root_org_uuid = str(uuid_root_org_uuid)
    converter.dataloader.load_mo_root_org_uuid.return_value = uuid_root_org_uuid  # type: ignore

    converter.org_unit_info = {
        uuids[0]: {**org_unit_infos[0], "parent_uuid": root_org_uuid},
        uuids[1]: {**org_unit_infos[1], "parent_uuid": org_unit_infos[0]["uuid"]},
    }

    org_unit_path_string = converter.org_unit_path_string_separator.join(org_units)
    org_units = [info["name"] for info in converter.org_unit_info.values()]

    assert "Magenta Aps" in org_units
    assert "Magenta Aarhus" in org_units
    assert "GrønlandsTeam" not in org_units

    # Create a unit with parents
    await converter.create_org_unit(org_unit_path_string)

    org_units = [info["name"] for info in converter.org_unit_info.values()]
    assert "GrønlandsTeam" in org_units

    # Try to create a unit without parents
    await converter.create_org_unit("Ørsted")
    org_units = [info["name"] for info in converter.org_unit_info.values()]
    assert "Ørsted" in org_units


async def test_get_or_create_org_unit_uuid(converter: LdapConverter):
    uuid_root_org_uuid = uuid4()
    root_org_uuid = str(uuid_root_org_uuid)
    converter.dataloader.load_mo_root_org_uuid.return_value = root_org_uuid  # type: ignore

    uuid = str(uuid4())
    converter.org_unit_info = {
        uuid: {"name": "Magenta Aps", "uuid": uuid, "parent_uuid": root_org_uuid}
    }

    # Get an organization UUID
    org_uuid = await converter.get_or_create_org_unit_uuid("Magenta Aps")
    assert org_uuid == uuid

    # Create a new organization and return its UUID
    uuid_magenta_aarhus = await converter.get_or_create_org_unit_uuid(
        "Magenta Aps\\Magenta Aarhus"
    )
    org_units = [info["name"] for info in converter.org_unit_info.values()]
    assert "Magenta Aarhus" in org_units

    with pytest.raises(UUIDNotFoundException):
        await converter.get_or_create_org_unit_uuid("")

    org_uuid = await converter.get_or_create_org_unit_uuid(
        "Magenta Aps\\Magenta Aarhus"
    )
    assert org_uuid == uuid_magenta_aarhus

    org_uuid = await converter.get_or_create_org_unit_uuid(
        "Magenta Aps \\ Magenta Aarhus"
    )
    assert org_uuid == uuid_magenta_aarhus


def test_clean_org_unit_path_string(converter: LdapConverter):
    assert converter.clean_org_unit_path_string("foo\\bar") == "foo\\bar"
    assert converter.clean_org_unit_path_string("foo \\ bar") == "foo\\bar"


def test_check_info_dict_for_duplicates(converter: LdapConverter):
    info_dict_with_duplicates = {
        uuid4(): {"user_key": "foo"},
        uuid4(): {"user_key": "foo"},
    }

    with pytest.raises(InvalidNameException):
        converter.check_info_dict_for_duplicates(info_dict_with_duplicates)


def test_check_org_unit_info_dict(converter: LdapConverter):
    # This name is invalid because it contains backslashes;
    # Because the org unit path separator is also a backslash.
    converter.org_unit_info = {uuid4(): {"name": "invalid\\name"}}
    with pytest.raises(InvalidNameException):
        converter.check_org_unit_info_dict()


def test_check_uuid_refs_in_mo_objects(converter: LdapConverter):
    converter.raw_mapping["username_generator"] = {}

    address_obj = {
        "objectClass": "ramodels.mo.details.address.Address",
        "_import_to_mo_": "true",
        "value": "val",
        "validity": "val",
        "address_type": "val",
    }

    converter.raw_mapping.update(
        {
            "ldap_to_mo": {
                "EmailEmployee": {
                    **address_obj,
                }
            }
        }
    )
    with pytest.raises(
        ValidationError, match="Either 'person' or 'org_unit' key needs to be present"
    ):
        parse_obj_as(ConversionMapping, converter.raw_mapping)

    converter.raw_mapping.update(
        {
            "ldap_to_mo": {
                "EmailEmployee": {
                    **address_obj,
                    "person": "{{ dict(uuid=employee_uuid or NONE) }}",
                    "org_unit": "{{ dict(uuid=employee_uuid or NONE) }}",
                }
            }
        }
    )
    with pytest.raises(
        ValidationError,
        match="Either 'person' or 'org_unit' key needs to be present.*Not both",
    ):
        parse_obj_as(ConversionMapping, converter.raw_mapping)

    converter.raw_mapping.update(
        {
            "ldap_to_mo": {
                "EmailEmployee": {
                    **address_obj,
                    "person": "{{ employee_uuid }}",
                }
            }
        }
    )
    with pytest.raises(
        ValidationError, match="Needs to be a dict with 'uuid' as one of its keys"
    ):
        parse_obj_as(ConversionMapping, converter.raw_mapping)

    converter.raw_mapping.update(
        {
            "ldap_to_mo": {
                "Employee": {
                    "objectClass": "ramodels.mo.employee.Employee",
                    "_import_to_mo_": "true",
                }
            }
        }
    )
    with pytest.raises(ValidationError, match="Needs to contain a key called 'uuid'"):
        parse_obj_as(ConversionMapping, converter.raw_mapping)

    converter.raw_mapping.update(
        {
            "ldap_to_mo": {
                "Employee": {
                    "objectClass": "ramodels.mo.employee.Employee",
                    "uuid": "{{ uuid4() }}",
                    "_import_to_mo_": "true",
                }
            }
        }
    )
    with pytest.raises(
        ValidationError, match="Needs to contain a reference to 'employee_uuid'"
    ):
        parse_obj_as(ConversionMapping, converter.raw_mapping)


def test_check_get_uuid_functions(converter: LdapConverter):
    converter.check_get_uuid_functions()

    converter.raw_mapping = converter.mapping = {
        "ldap_to_mo": {
            "Email": {
                "deprecated": True,
                "address_type": (
                    "{{ dict(uuid=get_employee_address_type_uuid('Email')) }}"
                ),
            }
        }
    }
    converter.check_get_uuid_functions()

    with pytest.raises(IncorrectMapping):
        converter.raw_mapping = converter.mapping = {
            "ldap_to_mo": {
                "Email": {
                    "address_type": (
                        "{{ dict(uuid=get_employee_address_type_uuid('typo')) }}"
                    ),
                }
            }
        }
        converter.check_get_uuid_functions()


def test_import_to_mo_and_export_to_ldap_(converter: LdapConverter):
    converter.raw_mapping = {
        "mo_to_ldap": {
            "Employee": {"_export_to_ldap_": "True"},
            "OrgUnit": {"_export_to_ldap_": "False"},
            "Address": {"_export_to_ldap_": "False"},
            "Mail": {"_export_to_ldap_": "Pause"},
        },
        "ldap_to_mo": {
            "Employee": {"_import_to_mo_": "False"},
            "OrgUnit": {"_import_to_mo_": "True"},
            "Address": {"_import_to_mo_": "manual_import_only"},
            "Mail": {"_import_to_mo_": "bad_flag"},
        },
    }

    assert converter._import_to_mo_("Employee", manual_import=False) is False
    assert converter._import_to_mo_("OrgUnit", manual_import=False) is True
    assert converter._import_to_mo_("Address", manual_import=True) is True
    assert converter._import_to_mo_("Address", manual_import=False) is False

    with pytest.raises(IncorrectMapping):
        converter._import_to_mo_("Mail", manual_import=False)

    assert converter._export_to_ldap_("Employee") is True
    assert converter._export_to_ldap_("OrgUnit") is False

    with pytest.raises(RequeueMessage):
        converter._export_to_ldap_("Mail")


@pytest.mark.parametrize(
    "overlay,expected",
    [
        (
            {
                "mo_to_ldap": {
                    "Employee": {**EMPLOYEE_OBJ, "_export_to_ldap_": "t"},
                },
                "ldap_to_mo": {
                    "Employee": {**EMPLOYEE_OBJ, "_import_to_mo_": "f"},
                },
            },
            "unexpected value; permitted: ",
        ),
        (
            {
                "mo_to_ldap": {
                    "Employee": {**EMPLOYEE_OBJ, "_export_to_ldap_": "true"},
                },
                "ldap_to_mo": {
                    "Employee": EMPLOYEE_OBJ,
                },
            },
            "_import_to_mo_\n  field required",
        ),
        (
            {
                "mo_to_ldap": {
                    "Employee": EMPLOYEE_OBJ,
                },
                "ldap_to_mo": {
                    "Employee": {**EMPLOYEE_OBJ, "_import_to_mo_": "true"},
                },
            },
            "_export_to_ldap_\n  field required",
        ),
        (
            {
                "mo_to_ldap": {
                    "Employee": {**EMPLOYEE_OBJ, "_export_to_ldap_": "True"},
                },
                "ldap_to_mo": {
                    "Employee": {**EMPLOYEE_OBJ, "_import_to_mo_": "True"},
                },
            },
            None,
        ),
    ],
)
def test_check_import_and_export_flags(
    converter: LdapConverter,
    overlay: dict[str, Any],
    expected: str | None,
) -> None:
    converter.raw_mapping["username_generator"] = {}

    converter.raw_mapping.update(overlay)
    if expected:
        with pytest.raises(ValidationError, match=expected):
            parse_obj_as(ConversionMapping, converter.raw_mapping)
    else:
        parse_obj_as(ConversionMapping, converter.raw_mapping)


async def test_find_ldap_it_system():
    settings = MagicMock()
    settings.ldap_unique_id_field = "objectGUID"

    template_str = "{{ ldap.objectGUID }}"
    template = environment.from_string(template_str)

    mapping = {"ldap_to_mo": {"AD": {"user_key": template}}}
    mo_it_systems = ["AD"]
    assert await find_ldap_it_system(settings, mapping, mo_it_systems) == "AD"

    mapping = {"ldap_to_mo": {"Wrong AD user_key": {"user_key": template}}}
    mo_it_systems = ["AD"]
    assert await find_ldap_it_system(settings, mapping, mo_it_systems) is None

    mapping = {"ldap_to_mo": {"AD": {"user_key": template}}}
    mo_it_systems = []
    assert await find_ldap_it_system(settings, mapping, mo_it_systems) is None

    mapping = {
        "ldap_to_mo": {
            "AD": {"user_key": template},
            "LDAP": {"user_key": template},
        }
    }
    mo_it_systems = ["AD", "LDAP"]
    assert await find_ldap_it_system(settings, mapping, mo_it_systems) is None


async def test_check_cpr_field_or_it_system(converter: LdapConverter):
    with patch(
        "mo_ldap_import_export.converters.find_cpr_field",
        return_value=None,
    ), patch(
        "mo_ldap_import_export.converters.find_ldap_it_system",
        return_value=None,
    ):
        with pytest.raises(
            IncorrectMapping,
            match="Neither a cpr-field or an ldap it-system could be found",
        ):
            await converter.check_cpr_field_or_it_system()


def test_check_info_dicts(converter: LdapConverter):
    uuid = str(uuid4())
    converter.all_info_dicts = {
        "my_info_dict": {uuid: {"uuid": uuid, "user_key": "foo"}}
    }
    converter.check_info_dicts()

    with pytest.raises(IncorrectMapping, match="not an uuid"):
        converter.all_info_dicts = {
            "my_info_dict": {uuid: {"uuid": "not_an_uuid", "user_key": "foo"}}
        }
        converter.check_info_dicts()

    with pytest.raises(IncorrectMapping, match="not a string"):
        converter.all_info_dicts = {
            "my_info_dict": {uuid: {"uuid": uuid4(), "user_key": "foo"}}
        }
        converter.check_info_dicts()

    with pytest.raises(IncorrectMapping, match="'uuid' key not found"):
        converter.all_info_dicts = {"my_info_dict": {uuid: {"user_key": "foo"}}}
        converter.check_info_dicts()


async def test_get_current_engagement_attribute(converter: LdapConverter):
    engagement1 = {
        "uuid": str(uuid4()),
        "user_key": "foo",
        "org_unit_uuid": str(uuid4()),
        "job_function_uuid": str(uuid4()),
        "engagement_type_uuid": str(uuid4()),
        "primary_uuid": str(uuid4()),
    }

    engagement2 = {
        "uuid": str(uuid4()),
        "user_key": "duplicate_user_key",
        "org_unit_uuid": str(uuid4()),
        "job_function_uuid": str(uuid4()),
        "engagement_type_uuid": str(uuid4()),
        "primary_uuid": None,
    }

    engagement3 = {
        "uuid": str(uuid4()),
        "user_key": "duplicate_user_key",
        "org_unit_uuid": str(uuid4()),
        "job_function_uuid": str(uuid4()),
        "engagement_type_uuid": str(uuid4()),
        "primary_uuid": None,
    }

    dataloader = AsyncMock()
    dataloader.load_mo_employee_engagement_dicts.return_value = [engagement1]
    converter.dataloader = dataloader

    test_attributes = [a for a in engagement1.keys() if a != "user_key"]

    for attribute in test_attributes:
        assert (
            await converter.get_current_engagement_attribute_uuid_dict(
                attribute, uuid4(), "foo"
            )
        )["uuid"] == engagement1[attribute]

    # Try for an employee without matching engagements
    with pytest.raises(UUIDNotFoundException):
        dataloader.load_mo_employee_engagement_dicts.return_value = []
        await converter.get_current_engagement_attribute_uuid_dict(
            attribute, uuid4(), "mucki"
        )

    # Try to find a duplicate engagement
    with pytest.raises(UUIDNotFoundException):
        dataloader.load_mo_employee_engagement_dicts.return_value = [
            engagement2,
            engagement3,
        ]
        await converter.get_current_engagement_attribute_uuid_dict(
            attribute, uuid4(), "duplicate_user_key"
        )

    # Try with faulty input
    with pytest.raises(ValueError, match="attribute must be an uuid-string"):
        await converter.get_current_engagement_attribute_uuid_dict(
            "user_key", uuid4(), "mucki"
        )


async def test_get_current_org_unit_uuid(converter: LdapConverter):
    uuid = str(uuid4())
    converter.get_current_engagement_attribute_uuid_dict = AsyncMock()  # type: ignore
    converter.get_current_engagement_attribute_uuid_dict.return_value = {"uuid": uuid}

    assert (await converter.get_current_org_unit_uuid_dict(uuid4(), "foo"))[
        "uuid"
    ] == uuid


async def test_get_current_engagement_type_uuid(converter: LdapConverter):
    uuid = str(uuid4())
    converter.get_current_engagement_attribute_uuid_dict = AsyncMock()  # type: ignore
    converter.get_current_engagement_attribute_uuid_dict.return_value = {"uuid": uuid}

    assert (await converter.get_current_engagement_type_uuid_dict(uuid4(), "foo"))[
        "uuid"
    ] == uuid


async def test_get_current_primary_uuid(converter: LdapConverter):
    uuid = str(uuid4())
    converter.get_current_engagement_attribute_uuid_dict = AsyncMock()  # type: ignore
    converter.get_current_engagement_attribute_uuid_dict.return_value = {"uuid": uuid}

    assert (await converter.get_current_primary_uuid_dict(uuid4(), "foo"))[
        "uuid"
    ] == uuid  # type: ignore

    converter.get_current_engagement_attribute_uuid_dict.return_value = {"uuid": None}

    assert await converter.get_current_primary_uuid_dict(uuid4(), "foo") is None


def test_clean_calls_to_get_current_method_from_template_string(
    converter: LdapConverter,
):
    template = "{{ ldap.foo or get_current_org_unit_uuid(ldap.bar) or None"

    cleaned_template = converter.clean_get_current_method_from_template_string(template)

    assert "get_current_org_unit_uuid" not in cleaned_template
    assert "ldap.bar" not in cleaned_template
    assert "or None" in cleaned_template
    assert "ldap.foo" in cleaned_template


async def test_get_org_unit_uuid_from_path(converter: LdapConverter):
    uuid_org1 = str(uuid4())
    uuid_org2 = str(uuid4())
    uuid_org3 = str(uuid4())

    uuid_root_org_uuid = uuid4()
    root_org_uuid = str(uuid_root_org_uuid)
    converter.dataloader.load_mo_root_org_uuid.return_value = uuid_root_org_uuid  # type: ignore

    converter.org_unit_info = {
        uuid_org1: {"name": "org1", "uuid": uuid_org1, "parent_uuid": root_org_uuid},
        uuid_org2: {"name": "org2", "uuid": uuid_org2, "parent_uuid": uuid_org1},
        uuid_org3: {"name": "org3", "uuid": uuid_org3, "parent_uuid": uuid_org2},
    }

    assert await converter.get_org_unit_uuid_from_path("org1\\org2\\org3") == uuid_org3
    assert await converter.get_org_unit_uuid_from_path("org1\\org2") == uuid_org2
    with pytest.raises(UUIDNotFoundException):
        await converter.get_org_unit_uuid_from_path("org1\\org4")
    with pytest.raises(UUIDNotFoundException):
        await converter.get_org_unit_uuid_from_path("org1\\org3")

    converter.org_unit_info = {
        uuid_org1: {
            "name": "org1",
            "uuid": uuid_org1,
            "parent_uuid": root_org_uuid,
        },
        uuid_org2: {
            "name": "org2",
            "uuid": uuid_org2,
            "parent_uuid": uuid_org1,
        },
        uuid_org3: {
            "name": "org3",
            "uuid": uuid_org3,
            "parent_uuid": uuid_org2,
        },
    }

    assert await converter.get_org_unit_uuid_from_path("org1\\org2\\org3") == uuid_org3
    assert await converter.get_org_unit_uuid_from_path("org1\\org2") == uuid_org2
    with pytest.raises(UUIDNotFoundException):
        await converter.get_org_unit_uuid_from_path("org1\\org4")


def test_org_unit_path_string_from_dn(converter: LdapConverter):
    dn = "CN=Angus,OU=Auchtertool,OU=Kingdom of Fife,OU=Scotland,DC=gh"

    org_unit_path = converter.org_unit_path_string_from_dn(dn)
    assert org_unit_path == "Scotland\\Kingdom of Fife\\Auchtertool"

    org_unit_path = converter.org_unit_path_string_from_dn(dn, 1)
    assert org_unit_path == "Kingdom of Fife\\Auchtertool"

    org_unit_path = converter.org_unit_path_string_from_dn(dn, 2)
    assert org_unit_path == "Auchtertool"

    org_unit_path = converter.org_unit_path_string_from_dn(dn, 3)
    assert org_unit_path == ""


def test_make_dn_from_org_unit_path(converter: LdapConverter):
    org_unit_path = " foo|mucki |bar"
    converter.org_unit_path_string_separator = "|"
    dn = "CN=Angus,OU=replace_me,DC=GHU"
    new_dn = converter.make_dn_from_org_unit_path(dn, org_unit_path)
    assert new_dn == "CN=Angus,OU=bar,OU=mucki,OU=foo,DC=GHU"


async def test_get_object_item_from_uuid(
    converter: LdapConverter, address_type_uuid: str
):
    # 'address_type_uuid' is loaded when converter.load_info_dicts() is called
    # Let's remove the employee_address_type_info dict to provoke a keyError
    converter.employee_address_type_info = {}

    # If all goes as intended, the info dict is reloaded and a keyError is NOT raised:
    with capture_logs() as cap_logs:
        user_key = await converter.get_employee_address_type_user_key(address_type_uuid)
        assert user_key == "Email"

        info_messages = [w for w in cap_logs if w["log_level"] == "info"]
        assert "Loading info dicts" in str(info_messages)

    # If we call the same function again, the info dicts are not reloaded:
    with capture_logs() as cap_logs:
        user_key = await converter.get_employee_address_type_user_key(address_type_uuid)
        assert user_key == "Email"

        info_messages = [w for w in cap_logs if w["log_level"] == "info"]
        assert "Loading info dicts" not in str(info_messages)

    # If an uuid really does not exist (not even after reloading) a keyError is raised:
    with pytest.raises(KeyError):
        await converter.get_employee_address_type_user_key(str(uuid4()))


def test_unutilized_init_elements(converter: LdapConverter) -> None:
    converter.raw_mapping["username_generator"] = {}

    converter.raw_mapping.update(
        {
            "init": {"it_systems": {"Whatever": "Whatever"}},
            "mo_to_ldap": {
                "Employee": {**EMPLOYEE_OBJ, "_export_to_ldap_": "True"},
            },
            "ldap_to_mo": {
                "Employee": {**EMPLOYEE_OBJ, "_import_to_mo_": "True"},
            },
        }
    )
    with pytest.raises(ValidationError, match="Unutilized elements in init"):
        parse_obj_as(ConversionMapping, converter.raw_mapping)


async def test_remove_first_org(converter: LdapConverter) -> None:
    result = converter.remove_first_org("")
    assert result == ""

    result = converter.remove_first_org("a\\b")
    assert result == "b"

    result = converter.remove_first_org("a\\b\\c")
    assert result == "b\\c"


async def test_get_primary_engagement_dict(converter: LdapConverter):
    engagement1 = {
        "uuid": str(uuid4()),
        "user_key": "foo",
        "org_unit_uuid": str(uuid4()),
        "job_function_uuid": str(uuid4()),
        "engagement_type_uuid": str(uuid4()),
        "primary_uuid": str(uuid4()),
    }

    engagement2 = {
        "uuid": str(uuid4()),
        "user_key": "bar",
        "org_unit_uuid": str(uuid4()),
        "job_function_uuid": str(uuid4()),
        "engagement_type_uuid": str(uuid4()),
        "primary_uuid": None,
    }

    engagement3 = {
        "uuid": str(uuid4()),
        "user_key": "baz",
        "org_unit_uuid": str(uuid4()),
        "job_function_uuid": str(uuid4()),
        "engagement_type_uuid": str(uuid4()),
        "primary_uuid": None,
    }

    employee_uuid = uuid4()

    dataloader = AsyncMock()
    converter.dataloader = dataloader

    # 3 engagements
    # -------------
    dataloader.load_mo_employee_engagement_dicts.return_value = [
        engagement1,
        engagement2,
        engagement3,
    ]
    # One primary
    # -----------
    dataloader.is_primaries.return_value = [True, False, False]
    result = await converter.get_primary_engagement_dict(employee_uuid)
    assert result["user_key"] == "foo"

    dataloader.is_primaries.return_value = [False, True, False]
    result = await converter.get_primary_engagement_dict(employee_uuid)
    assert result["user_key"] == "bar"

    dataloader.is_primaries.return_value = [False, False, True]
    result = await converter.get_primary_engagement_dict(employee_uuid)
    assert result["user_key"] == "baz"

    # Two primaries
    # -------------
    with pytest.raises(ValueError) as exc_info:
        dataloader.is_primaries.return_value = [False, True, True]
        await converter.get_primary_engagement_dict(employee_uuid)
    assert "Expected exactly one item in iterable" in str(exc_info.value)

    # No primary
    # ----------
    with pytest.raises(ValueError) as exc_info:
        dataloader.is_primaries.return_value = [False, False, False]
        await converter.get_primary_engagement_dict(employee_uuid)
    assert "too few items in iterable (expected 1)" in str(exc_info.value)

    # 1 engagement
    # ------------
    dataloader.load_mo_employee_engagement_dicts.return_value = [engagement3]
    # One primary
    # -----------
    dataloader.is_primaries.return_value = [True]
    result = await converter.get_primary_engagement_dict(employee_uuid)
    assert result["user_key"] == "baz"

    # No primary
    with pytest.raises(ValueError) as exc_info:
        dataloader.is_primaries.return_value = [False]
        await converter.get_primary_engagement_dict(employee_uuid)
    assert "too few items in iterable (expected 1)" in str(exc_info.value)

    # 0 engagements
    # -------------
    with pytest.raises(ValueError) as exc_info:
        dataloader.load_mo_employee_engagement_dicts.return_value = []
        dataloader.is_primaries.return_value = []
        await converter.get_primary_engagement_dict(employee_uuid)
    assert "too few items in iterable (expected 1)" in str(exc_info.value)


async def test_get_employee_dict(converter: LdapConverter) -> None:
    cpr_no = "1407711900"
    uuid = uuid4()
    mo_employee = Employee(**{"cpr_no": cpr_no, "uuid": uuid})

    dataloader = AsyncMock()
    converter.dataloader = dataloader
    dataloader.load_mo_employee.return_value = mo_employee

    result = await converter.get_employee_dict(uuid)
    assert result == {
        "details": None,
        "givenname": None,
        "name": None,
        "nickname": None,
        "nickname_givenname": None,
        "nickname_surname": None,
        "org": None,
        "seniority": None,
        "surname": None,
        "type_": "employee",
        "user_key": str(uuid),
        "uuid": uuid,
        "cpr_no": cpr_no,
    }


async def test_ldap_to_mo_termination(converter: LdapConverter) -> None:
    employee_uuid = uuid4()
    result = await converter.from_ldap(
        LdapObject(
            dn="",
            mail="foo@bar.dk",
            mail_validity_from=datetime.datetime(2019, 1, 1, 0, 10, 0),
        ),
        "Email",
        employee_uuid=employee_uuid,
    )
    mail = one(result)
    assert not hasattr(mail, "terminate_")
    assert mail.value == "foo@bar.dk"
    assert mail.person.uuid == employee_uuid

    # Add _terminate_ key to Email mapping
    converter.context["user_context"]["mapping"]["ldap_to_mo"]["Email"][
        "_terminate_"
    ] = "{{ now()|mo_datestring }}"
    await converter._init()
    result = await converter.from_ldap(
        LdapObject(
            dn="",
            mail="foo@bar.dk",
            mail_validity_from=datetime.datetime(2019, 1, 1, 0, 10, 0),
        ),
        "Email",
        employee_uuid=employee_uuid,
    )
    mail = one(result)
    assert hasattr(mail, "terminate_")
    assert mail.value == "foo@bar.dk"
    assert mail.person.uuid == employee_uuid
