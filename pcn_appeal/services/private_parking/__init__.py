"""Private parking: the existing appeal service, behind the common interface.

Every step delegates to AppealPipeline unchanged - Case Intelligence, the Claim
Plan, PoFA, questions, drafting and validation are exactly what they were. What
this module adds is the route boundary: which uploads this service needs before
it reads anything, and which private-parking stages it does not handle.
"""
from __future__ import annotations

from typing import Optional

from ...models import CaseFile
from ...notice_completeness import upload_pages_sufficient
from ...rules import scope
from ...rules.scope import ScopeStop
from ..base import CompletenessPolicy, ServiceEngine

ROUTE = "PRIVATE_PARKING"

FRONT_AND_BACK = CompletenessPolicy(
    "FRONT_AND_BACK_OR_MULTIPAGE",
    "Both sides of the notice as distinct images, or a multipage PDF of the whole notice.",
    check=lambda case: upload_pages_sufficient(list(case.evidence.values())))

# DRAFT customer wording - for client approval.
APPEAL_RESPONSE_STOP = ScopeStop(
    code="PRIVATE_APPEAL_RESPONSE",
    title="The parking company has already replied to an appeal",
    message=("This letter is the parking company's response to an appeal, so the "
             "charge has moved past the first appeal stage our service covers."),
    recommendation=("The parking company's letter should explain the independent "
                    "appeals service and its deadline. We don't prepare independent "
                    "appeals yet."),
    cta_label="See our free resources",
    cta_action="RESOURCES")

# Stages of a private parking document this service does not take. Anything
# else on the private route proceeds into the pipeline, whose own scope gate
# still runs as defence in depth.
STAGE_STOPS: dict[str, ScopeStop] = {
    "APPEAL_WINDOW_CLOSED": scope.STOPS["OUT_OF_STAGE"],
    "OPERATOR_RESPONSE": APPEAL_RESPONSE_STOP,
}


class PrivateParkingService(ServiceEngine):
    route = ROUTE
    live = True
    completeness = FRONT_AND_BACK

    def __init__(self, pipeline=None):
        # AppealPipeline. Optional so the route's stops and policy can be read
        # without building an LLM client.
        self.pipeline = pipeline

    def outcome(self, case: CaseFile) -> Optional[ScopeStop]:
        return STAGE_STOPS.get(case.stage or "")

    def _pipe(self):
        if self.pipeline is None:
            raise RuntimeError("PrivateParkingService needs an AppealPipeline for this step")
        return self.pipeline

    def extract_service_facts(self, case: CaseFile) -> list[str]:
        return self._pipe().ingest(case)

    def analyse(self, case: CaseFile):
        return self._pipe().analysis_of(case, case.raw_answers.get("narrative", ""))

    def get_questions(self, case: CaseFile) -> list[dict]:
        return list(case.pending_questions)

    def generate(self, case: CaseFile):
        return self._pipe().generate(case)

    def validate_output(self, case: CaseFile, output):
        return output.validation
