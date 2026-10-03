"use client";

import { useEffect, useState } from "react";
import { wantsTrace } from "@/lib/trace";
import CaseTraceConsole from "./CaseTraceConsole";

/**
 * Mounts the Case Intelligence Trace only when `?trace=1` is present.
 * Authorisation is enforced inside the console (session cookie + admin token).
 * Query string alone never unlocks data.
 */
export default function TraceGate({ caseId }: { caseId: string }) {
  const [enabled, setEnabled] = useState(false);

  useEffect(() => {
    setEnabled(wantsTrace(window.location.search));
  }, []);

  if (!enabled) return null;
  return <CaseTraceConsole caseId={caseId} />;
}
