# SPDX-FileCopyrightText: 2019-2020 Magenta ApS
# SPDX-License-Identifier: MPL-2.0
from typing import Any
from typing import Literal
from typing import cast

from fastramqpi.context import Context
from pydantic import Extra
from pydantic import Field
from ramodels.mo._shared import UUID
from ramodels.mo._shared import JobFunction
from ramodels.mo._shared import MOBase
from ramodels.mo._shared import PersonRef

from .autogenerated_graphql_client import GraphQLClient


class CustomerSpecific(MOBase, extra=Extra.allow):  # type: ignore
    async def sync_to_mo(self, context: Context) -> list:
        return []

    async def sync_to_ldap(self):
        pass


class JobTitleFromADToMO(CustomerSpecific):
    user: PersonRef = Field(
        description=("Reference to the employee of the created engagement object.")
    )
    job_function: JobFunction | None = Field(
        description=(
            "Reference to the job function class for the created engagement object."
        ),
        default=None,
    )
    job_function_fallback: JobFunction = Field(
        description=(
            "Reference to the job function class for the created engagement object."
        )
    )
    type_: Literal["jobtitlefromadtomo"] = Field(
        "jobtitlefromadtomo", alias="type", description="The object type."
    )

    @classmethod
    def from_simplified_fields(
        cls,
        user_uuid: UUID,
        job_function_uuid: UUID | None,
        job_function_fallback_uuid: UUID,
        **kwargs,
    ) -> "JobTitleFromADToMO":
        """Create an jobtitlefromadtomo from simplified fields."""
        user = PersonRef(uuid=user_uuid)
        job_function = JobFunction(uuid=job_function_uuid)
        job_function_fallback = JobFunction(uuid=job_function_fallback_uuid)
        return cls(
            user=user,
            job_function=job_function,
            job_function_fallback=job_function_fallback,
            **kwargs,
        )

    async def sync_to_mo(self, context: Context):
        graphql_client = cast(GraphQLClient, context["graphql_client"])

        async def get_engagement_details(employee_uuid: UUID) -> list[dict[str, Any]]:
            result = await graphql_client.read_engagements_by_employee_uuid(
                employee_uuid
            )

            return [
                {
                    "uuid": res.current.uuid,
                    "from_": res.current.validity.from_,
                    "to": res.current.validity.to,
                }
                for res in result.objects
                if res.current is not None
            ]

        async def set_job_title(engagement_details: list):
            job_func = self.job_function_fallback.uuid
            if self.job_function is not None:
                job_func = self.job_function.uuid
            # obj is the dict sent from get engagements
            return [
                {
                    "uuid_to_ignore": obj["uuid"],
                    "task": graphql_client.set_job_title(job_function=job_func, **obj),
                }
                for obj in engagement_details
            ]

        engagement_details = await get_engagement_details(employee_uuid=self.user.uuid)
        return await set_job_title(engagement_details=engagement_details)
