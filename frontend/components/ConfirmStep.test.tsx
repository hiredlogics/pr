import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import ConfirmStep from "./ConfirmStep";

afterEach(cleanup);
const details = [{ name: "vrm", label: "Vehicle registration", value: "KS58OPW", needs_attention: true }];

describe("explicit notice confirmation", () => {
  it("does not confirm a flagged value through the blanket continue button", () => {
    const confirm = vi.fn();
    render(<ConfirmStep details={details} busy={false} onConfirm={confirm} />);
    fireEvent.click(screen.getByRole("button", { name: "Looks correct, continue" }));
    expect(confirm).toHaveBeenCalledWith({});
  });
  it("sends an explicitly checked field even when its characters did not change", () => {
    const confirm = vi.fn();
    render(<ConfirmStep details={details} busy={false} onConfirm={confirm} />);
    fireEvent.click(screen.getByRole("checkbox"));
    fireEvent.click(screen.getByRole("button", { name: "Save changes and continue" }));
    expect(confirm).toHaveBeenCalledWith({ vrm: "KS58OPW" });
  });
  it("sends the customer's corrected reading", () => {
    const confirm = vi.fn();
    render(<ConfirmStep details={details} busy={false} onConfirm={confirm} />);
    fireEvent.change(screen.getByRole("textbox"), { target: { value: "AB12CDE" } });
    fireEvent.click(screen.getByRole("button", { name: "Save changes and continue" }));
    expect(confirm).toHaveBeenCalledWith({ vrm: "AB12CDE" });
  });
});
