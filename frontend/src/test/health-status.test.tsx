import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { HealthStatus } from "../components/HealthStatus";

describe("HealthStatus", () => {
  it("shows the provider, model and a reachable state", () => {
    render(<HealthStatus provider="ollama" model="qwen2.5:3b-instruct" state="reachable" />);
    expect(screen.getByText("Ollama")).toBeInTheDocument();
    expect(screen.getByText("qwen2.5:3b-instruct")).toBeInTheDocument();
    expect(screen.getByText("reachable")).toBeInTheDocument();
  });

  it("flags an unreachable provider in words, not just colour", () => {
    render(<HealthStatus provider="ollama" model="qwen2.5:3b-instruct" state="unreachable" />);
    expect(screen.getByText("Ollama")).toBeInTheDocument();
    expect(screen.getByText("unreachable")).toBeInTheDocument();
    expect(screen.queryByText("qwen2.5:3b-instruct")).not.toBeInTheDocument();
  });

  it("shows a loading placeholder before health data arrives", () => {
    render(<HealthStatus state="checking" />);
    expect(screen.getByText("Checking LLM…")).toBeInTheDocument();
  });
});
