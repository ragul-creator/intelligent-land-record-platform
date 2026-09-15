import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { App } from "../App";

describe("App", () => {
  it("renders the platform heading", () => {
    render(<App />);

    expect(
      screen.getByRole("heading", { name: /intelligent land record platform/i }),
    ).toBeInTheDocument();
  });
});
