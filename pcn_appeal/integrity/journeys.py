"""Journey regression harness (P5.5 §8).

A journey file is INPUT ONLY: the documents a customer uploaded, what they
said, and how they answer whatever the system asks. The harness drives the
real customer journey over HTTP - the same routes the browser calls - then
fetches the admin audit for the case and decides PASS / FAIL from:

  * the integrity checks (always, for every journey);
  * the journey's own expectations, when it states any;
  * determinism, when `repeat` > 1: every run must lock the same claim plan.

It runs in-process (FastAPI TestClient) or against a deployed API
(`base_url`), with the same code, so a staging run exercises the real model
and the real database.

journeys/<name>.yaml
    case_id: REG_001
    documents:
      - evidence_id: E1
        text: |                       # or  file: notice.pdf  (relative to the yaml)
          PARKING CHARGE NOTICE
          Operator Name: ...
    customer:
      narrative: "..."
      driver_already_named_to_operator: null
    answers:                          # by fact name, used when that fact is asked
      multiple_visits: yes
    answer_policy: skip               # for anything else asked: skip | yes | no | dont_know
    expect:                           # all optional
      state: RELEASED
      outcome: NO_SUPPORTED_GROUNDS
      approved_includes: [KB-BAY-02]
      approved_excludes: [KB-ANPR-01]
      letter_contains: ["PCN778899"]
      letter_excludes: ["I parked"]
      asks: [multiple_visits]         # facts that must be asked (any round)
      needs_documents: true           # the journey deliberately stops at a document request
    repeat: 1
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

MAX_ROUNDS = 6
POLICIES = ("skip", "yes", "no", "dont_know", "none")


@dataclass
class JourneyResult:
    name: str
    case_ids: list[str]
    passed: bool
    state: Optional[str]
    outcome: Optional[str]
    approved: list[str]
    failures: list[str] = field(default_factory=list)
    checks: list[dict] = field(default_factory=list)
    report: str = ""
    report_path: Optional[str] = None
    plan_digests: list[Optional[str]] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {"journey": self.name, "result": "PASS" if self.passed else "FAIL",
                "case_ids": self.case_ids, "state": self.state, "outcome": self.outcome,
                "approved": self.approved, "failures": self.failures,
                "checks": [(c["check"], c["status"]) for c in self.checks],
                "report": self.report_path}


def load_journey(path: Path) -> dict:
    import yaml
    data = yaml.safe_load(path.read_text()) or {}
    data.setdefault("case_id", path.stem)
    data["_path"] = str(path)
    return data


def _word(value: Any) -> Any:
    """YAML reads a bare `yes` / `no` as a boolean; the customer says a word."""
    if value is True:
        return "yes"
    if value is False:
        return "no"
    return value


def _answer_for(q: dict, answers: dict, policy: str) -> Any:
    fact = q.get("fact")
    if fact in answers:
        return answers[fact]
    if policy == "yes":
        return 18 if q.get("type") == "int" else "yes"
    if policy == "no":
        return 0 if q.get("type") == "int" else "no"
    if policy == "dont_know":
        return "I don't know"
    return None


class JourneyRunner:
    """`client` is any httpx-compatible client: fastapi.testclient.TestClient
    for in-process runs, httpx.Client(base_url=...) for a deployed API."""

    def __init__(self, client, admin_token: Optional[str] = None,
                 out_dir: Optional[Path] = None):
        self.client = client
        self.headers = {"X-Admin-Token": admin_token} if admin_token else {}
        self.out_dir = Path(out_dir) if out_dir else None

    # ------------------------------------------------------------ drive
    def drive(self, journey: dict) -> dict:
        """One customer journey. Returns the final customer payload plus the
        facts that were asked along the way."""
        base = Path(journey.get("_path") or ".").parent
        customer = journey.get("customer") or {}
        answers = dict(journey.get("answers") or {})
        policy = _word(journey.get("answer_policy", "skip"))
        answers = {k: _word(v) for k, v in answers.items()}
        if policy not in POLICIES:
            raise ValueError(f"answer_policy must be one of {POLICIES}, not {policy!r}")
        docs = journey.get("documents") or []
        files = [d for d in docs if d.get("file")]
        if files:
            payload = [("files", (Path(d["file"]).name, (base / d["file"]).read_bytes()))
                       for d in files]
            data = {"narrative": customer.get("narrative") or ""}
            if customer.get("driver_already_named_to_operator") is not None:
                data["driver_already_named_to_operator"] = str(
                    customer["driver_already_named_to_operator"])
            r = self.client.post("/appeal/files", files=payload, data=data)
        else:
            r = self.client.post("/appeal", json={
                "documents": [{"evidence_id": d.get("evidence_id") or f"E{i}",
                               "filename": d.get("filename") or f"doc{i}.txt",
                               "kind": d.get("kind") or "OTHER", "text": d.get("text") or ""}
                              for i, d in enumerate(docs, 1)],
                "narrative": customer.get("narrative") or "",
                "driver_already_named_to_operator": customer.get(
                    "driver_already_named_to_operator")})
        r.raise_for_status()
        result = r.json()
        case_id = result["case_id"]
        asked: list[str] = []
        for _ in range(MAX_ROUNDS):
            questions = result.get("questions") or []
            if not questions or result.get("letter") or result.get("stop_code"):
                break
            if result.get("outcome") == "NEEDS_DOCUMENTS" or all(
                    q.get("fact") == "notice_reverse_pages" for q in questions):
                # a document request, not a question: the journey did not
                # supply what the customer would have to upload
                result["_needs_documents"] = True
                break
            asked += [q.get("fact") for q in questions]
            given = {q["fact"]: _answer_for(q, answers, policy) for q in questions}
            given = {k: v for k, v in given.items() if v is not None}
            body = {"answers": given} if given else {"skip": True}
            r = self.client.post(f"/appeal/{case_id}", json=body)
            r.raise_for_status()
            result = r.json()
        else:
            result["_loop"] = True
        result["_asked"] = asked
        return result

    def audit(self, case_id: str) -> dict:
        r = self.client.get(f"/admin/cases/{case_id}/audit", headers=self.headers)
        r.raise_for_status()
        return r.json()

    # -------------------------------------------------------------- run
    def run(self, journey: dict) -> JourneyResult:
        name = journey.get("case_id") or "journey"
        repeat = max(1, int(journey.get("repeat") or 1))
        expect = journey.get("expect") or {}
        failures: list[str] = []
        case_ids, digests, approved_runs = [], [], []
        final, audit = {}, {}
        for _ in range(repeat):
            final = self.drive(journey)
            case_ids.append(final["case_id"])
            if final.get("_loop"):
                failures.append(f"still asking after {MAX_ROUNDS} rounds")
            if final.get("_needs_documents") and not expect.get("needs_documents"):
                failures.append("the system asked for documents the journey does not supply "
                                "(add the reverse page, or expect: needs_documents: true)")
            audit = self.audit(final["case_id"])
            plan = audit.get("claim_plan") or {}
            digests.append(plan.get("plan_digest"))
            approved_runs.append(tuple(plan.get("approved") or []))
        checks = (audit.get("integrity") or {}).get("checks") or []
        failures += [f"integrity {c['check']}: {json.dumps(c.get('detail'), default=str)[:200]}"
                     for c in checks if c["status"] != "PASS"]
        if repeat > 1 and (len(set(digests)) > 1 or len(set(approved_runs)) > 1):
            failures.append(f"not deterministic over {repeat} runs: plans {approved_runs}")
        approved = list(approved_runs[-1]) if approved_runs else []
        state, outcome = final.get("state"), final.get("outcome")
        letter = final.get("letter") or ""
        failures += self._expectations(expect, state, outcome, approved, letter,
                                       final.get("_asked") or [])
        report = audit.get("report") or ""
        path = None
        if self.out_dir and report:
            self.out_dir.mkdir(parents=True, exist_ok=True)
            p = self.out_dir / f"{name}_CASE_REPORT.md"
            verdict = "PASS" if not failures else "FAIL"
            p.write_text(f"Journey: {name}  Result: {verdict}\n\n"
                         + ("\n".join(f"- {f}" for f in failures) + "\n\n" if failures else "")
                         + report)
            path = str(p)
        return JourneyResult(name, case_ids, not failures, state, outcome, approved, failures,
                             checks, report, path, digests)

    @staticmethod
    def _expectations(expect: dict, state, outcome, approved, letter, asked) -> list[str]:
        out = []
        if expect.get("state") and state != expect["state"]:
            out.append(f"expected state {expect['state']}, got {state}")
        if expect.get("outcome") and outcome != expect["outcome"]:
            out.append(f"expected outcome {expect['outcome']}, got {outcome}")
        for mid in expect.get("approved_includes") or []:
            if mid not in approved:
                out.append(f"expected {mid} in the claim plan, got {approved}")
        for mid in expect.get("approved_excludes") or []:
            if mid in approved:
                out.append(f"expected {mid} NOT in the claim plan")
        for text in expect.get("letter_contains") or []:
            if text not in letter:
                out.append(f"letter should contain {text!r}")
        for text in expect.get("letter_excludes") or []:
            if text in letter:
                out.append(f"letter must not contain {text!r}")
        for fact in expect.get("asks") or []:
            if fact not in asked:
                out.append(f"expected the customer to be asked {fact}, asked {asked}")
        return out


def run_directory(runner: JourneyRunner, directory: Path) -> list[JourneyResult]:
    results = []
    for path in sorted(Path(directory).glob("*.yaml")) + sorted(Path(directory).glob("*.yml")):
        results.append(runner.run(load_journey(path)))
    return results


def summary(results: list[JourneyResult]) -> dict:
    return {"journeys": len(results), "passed": sum(1 for r in results if r.passed),
            "failed": [r.name for r in results if not r.passed],
            "results": [r.as_dict() for r in results]}


__all__ = ["JourneyRunner", "JourneyResult", "load_journey", "run_directory", "summary"]
