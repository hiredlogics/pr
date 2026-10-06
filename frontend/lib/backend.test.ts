import { describe, expect, it } from "vitest";
import { backendUrl } from "./backend";

describe("backend deployment configuration", () => {
  it("rejects an empty production origin instead of forwarding to a relative URL", () => {
    expect(backendUrl("", true)).toBeNull();
    expect(backendUrl("   ", true)).toBeNull();
  });

  it("normalizes the configured Railway origin", () => {
    expect(backendUrl(" https://api-p7-production.up.railway.app/ ", true))
      .toBe("https://api-p7-production.up.railway.app");
  });

  it("keeps local development working without deployment settings", () => {
    expect(backendUrl("", false)).toBe("http://127.0.0.1:8077");
  });
});
