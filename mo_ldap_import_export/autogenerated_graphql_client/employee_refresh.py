from uuid import UUID

from .base_model import BaseModel


class EmployeeRefresh(BaseModel):
    employee_refresh: "EmployeeRefreshEmployeeRefresh"


class EmployeeRefreshEmployeeRefresh(BaseModel):
    objects: list[UUID]


EmployeeRefresh.update_forward_refs()
EmployeeRefreshEmployeeRefresh.update_forward_refs()
