/** Stages the page actually renders. */
export type Stage = "upload" | "questions" | "result";

/** Plus the transient state while extraction runs, which the rail shows as its
 *  own step ("Check details") because it is what the customer is waiting on. */
export type RailStage = Stage | "reading";

/**
 * Payment is in the design but nothing is wired to a provider yet, so the step
 * is declared and switched off rather than shown as a stage that never arrives.
 * Flip this on in the same commit that adds the payment screen.
 */
const PAYMENT_ENABLED = false;

type Step = { id: RailStage | "payment"; label: string; enabled?: boolean };

const STEPS: Step[] = [
  { id: "upload", label: "Upload" },
  { id: "reading", label: "Check details" },
  { id: "questions", label: "Your situation" },
  { id: "payment", label: "Payment", enabled: PAYMENT_ENABLED },
  { id: "result", label: "Download" },
];

export default function ProgressRail({ stage, resultLabel = "Download" }: { stage: RailStage; resultLabel?: string }) {
  const steps = STEPS.filter((s) => s.enabled !== false);
  const current = steps.findIndex((s) => s.id === stage);

  return (
    <ol className="rail" aria-label="Progress">
      {steps.map((step, i) => {
        const status = i < current ? "done" : i === current ? "active" : "todo";
        return (
          <li
            key={step.id}
            data-status={status}
            aria-current={status === "active" ? "step" : undefined}
          >
            <span className="bar" aria-hidden="true">
              {status === "done" ? "✓" : i + 1}
            </span>
            <span>{step.id === "result" ? resultLabel : step.label}</span>
          </li>
        );
      })}
    </ol>
  );
}
