from typing import Optional
from uuid import UUID

from .base_model import BaseModel


class ReadEmployeeUuidByItuserUserKey(BaseModel):
    itusers: "ReadEmployeeUuidByItuserUserKeyItusers"


class ReadEmployeeUuidByItuserUserKeyItusers(BaseModel):
    objects: list["ReadEmployeeUuidByItuserUserKeyItusersObjects"]


class ReadEmployeeUuidByItuserUserKeyItusersObjects(BaseModel):
    current: Optional["ReadEmployeeUuidByItuserUserKeyItusersObjectsCurrent"]


class ReadEmployeeUuidByItuserUserKeyItusersObjectsCurrent(BaseModel):
    employee_uuid: UUID | None


ReadEmployeeUuidByItuserUserKey.update_forward_refs()
ReadEmployeeUuidByItuserUserKeyItusers.update_forward_refs()
ReadEmployeeUuidByItuserUserKeyItusersObjects.update_forward_refs()
ReadEmployeeUuidByItuserUserKeyItusersObjectsCurrent.update_forward_refs()
