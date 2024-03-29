# SPDX-FileCopyrightText: 2019-2020 Magenta ApS
# SPDX-License-Identifier: MPL-2.0
"""Dependency injection helpers."""
from typing import Annotated

from fastapi import Depends
from fastramqpi.depends import from_user_context
from fastramqpi.ramqp import AMQPSystem as _AMQPSystem
from fastramqpi.ramqp.depends import from_context

from .autogenerated_graphql_client import GraphQLClient as _GraphQLClient
from .config import Settings as _Settings
from .converters import LdapConverter as _LdapConverter
from .dataloaders import DataLoader as _DataLoader
from .import_export import SyncTool as _SyncTool
from .ldap import Connection as _Connection

GraphQLClient = Annotated[_GraphQLClient, Depends(from_context("graphql_client"))]
SyncTool = Annotated[_SyncTool, Depends(from_user_context("sync_tool"))]
DataLoader = Annotated[_DataLoader, Depends(from_user_context("dataloader"))]
Settings = Annotated[_Settings, Depends(from_user_context("settings"))]
LdapConverter = Annotated[_LdapConverter, Depends(from_user_context("converter"))]
Connection = Annotated[_Connection, Depends(from_user_context("ldap_connection"))]
InternalAMQPSystem = Annotated[
    _AMQPSystem, Depends(from_user_context("internal_amqpsystem"))
]
