import { describe, it, expect } from "vitest";
import { isLocalHost, rejectReason } from "./local-request.js";

describe("isLocalHost", () => {
  it("accepts loopback names with and without a port", () => {
    for (const host of ["localhost", "localhost:3847", "127.0.0.1:3847", "[::1]:3847", "[::1]"]) {
      expect(isLocalHost(host)).toBe(true);
    }
  });

  it("rejects other names, including look-alikes", () => {
    for (const host of ["evil.com", "localhost.evil.com", "127.0.0.1.evil.com:3847", "192.168.1.5:3847", "", undefined]) {
      expect(isLocalHost(host)).toBe(false);
    }
  });
});

describe("rejectReason", () => {
  const host = "localhost:3847";

  it("blocks a rebound Host", () => {
    expect(rejectReason("GET", { host: "evil.com:3847" })).toBe("Forbidden host");
  });

  it("blocks a state-changing request from a foreign origin", () => {
    expect(rejectReason("POST", { host, origin: "https://evil.com" })).toBe("Forbidden origin");
    expect(rejectReason("POST", { host, origin: "null" })).toBe("Forbidden origin");
    expect(rejectReason("POST", { host, origin: "http://localhost:9999" })).toBe("Forbidden origin");
  });

  it("allows same-origin and origin-less requests", () => {
    expect(rejectReason("POST", { host, origin: "http://localhost:3847" })).toBeNull();
    expect(rejectReason("POST", { host })).toBeNull();
    expect(rejectReason("GET", { host, origin: "https://evil.com" })).toBeNull();
  });

  it("skips the Host check when the studio is bound to the LAN", () => {
    expect(rejectReason("GET", { host: "192.168.1.5:3847" }, true)).toBeNull();
    expect(rejectReason("POST", { host: "192.168.1.5:3847", origin: "https://evil.com" }, true)).toBe("Forbidden origin");
  });
});
