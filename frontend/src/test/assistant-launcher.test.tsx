import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { AssistantLauncher } from "../components/AssistantLauncher";

function renderLauncher(health: unknown) {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
    ok: true,
    status: 200,
    text: async () => JSON.stringify(health),
  }));
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter><AssistantLauncher /></MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("AssistantLauncher", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("is a link to the Ask page and shows it is ready when the model is reachable", async () => {
    const { container } = renderLauncher({ status: "ok", provider: "ollama", model: "qwen2.5:3b-instruct", llm_reachable: true, now: "2026-11-05T17:00" });
    const link = screen.getByRole("link", { name: "Ask ShiftGuard" });
    expect(link).toHaveAttribute("href", "/assistant");
    await vi.waitFor(() => expect(container.querySelector(".assistant-launcher.is-ready")).not.toBeNull());
  });

  it("stays a working link but is not marked ready while the model is down", async () => {
    const { container } = renderLauncher({ status: "ok", provider: "ollama", model: "qwen2.5:3b-instruct", llm_reachable: false, now: "2026-11-05T17:00" });
    expect(screen.getByRole("link", { name: "Ask ShiftGuard" })).toHaveAttribute("href", "/assistant");
    await new Promise((resolve) => setTimeout(resolve, 50));
    expect(container.querySelector(".assistant-launcher.is-ready")).toBeNull();
  });
});
