import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";

import { App } from "../App";

describe("App", () => {
  it("renders the platform heading", () => {
    render(<MemoryRouter><App /></MemoryRouter>);

    expect(
      screen.getByRole("heading", { name: /intelligent land record platform/i }),
    ).toBeInTheDocument();
  });
});
