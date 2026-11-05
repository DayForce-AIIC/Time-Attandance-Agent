import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { UserMenu } from "../components/UserMenu";

describe("UserMenu", () => {
  it("shows the account email and role, and reveals Sign out only after it is opened", async () => {
    const user = userEvent.setup();
    const onSignOut = vi.fn();
    render(<UserMenu email="hr.reviewer@example.com" role="HR" onSignOut={onSignOut} />);

    expect(screen.getByText("hr.reviewer@example.com")).toBeInTheDocument();
    expect(screen.getByText("HR")).toBeInTheDocument();
    expect(screen.queryByRole("menuitem", { name: "Sign out" })).not.toBeInTheDocument();

    const trigger = screen.getByRole("button", { name: /account menu/i });
    expect(trigger).toHaveAttribute("aria-expanded", "false");
    await user.click(trigger);
    expect(trigger).toHaveAttribute("aria-expanded", "true");

    await user.click(screen.getByRole("menuitem", { name: "Sign out" }));
    expect(onSignOut).toHaveBeenCalledOnce();
    expect(screen.queryByRole("menuitem", { name: "Sign out" })).not.toBeInTheDocument();
  });

  it("closes on Escape and on an outside click without signing out", async () => {
    const user = userEvent.setup();
    const onSignOut = vi.fn();
    render(
      <div>
        <button type="button">Elsewhere</button>
        <UserMenu email="hr.reviewer@example.com" role="HR" onSignOut={onSignOut} />
      </div>,
    );
    const trigger = screen.getByRole("button", { name: /account menu/i });

    await user.click(trigger);
    expect(screen.getByRole("menuitem", { name: "Sign out" })).toBeInTheDocument();
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("menuitem", { name: "Sign out" })).not.toBeInTheDocument();
    expect(trigger).toHaveFocus();

    await user.click(trigger);
    await user.click(screen.getByRole("button", { name: "Elsewhere" }));
    expect(screen.queryByRole("menuitem", { name: "Sign out" })).not.toBeInTheDocument();
    expect(onSignOut).not.toHaveBeenCalled();
  });
});
