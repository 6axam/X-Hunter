import { describe, expect, it } from "vitest";
import type { Finding } from "../types";
import { formatPrice } from "./FindingsPage";

const finding = { price: "18500.00", currency: "UAH" } as Finding;

describe("formatPrice", () => {
  it("formats UAH without floating point calculations", () => {
    expect(formatPrice(finding)).toContain("18");
    expect(formatPrice(finding)).toContain("грн");
  });

  it("does not invent a missing price", () => {
    expect(formatPrice({ ...finding, price: null })).toBe("Цена не указана");
  });
});
