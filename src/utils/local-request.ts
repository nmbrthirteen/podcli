const LOCAL_HOSTNAMES = new Set(["localhost", "127.0.0.1", "[::1]"]);
const SAFE_METHODS = new Set(["GET", "HEAD", "OPTIONS"]);

function hostnameOf(host: string): string | null {
  try {
    return new URL(`http://${host}`).hostname;
  } catch {
    return null;
  }
}

/** True when a Host header names this machine. Any port is accepted. */
export function isLocalHost(host: string | undefined): boolean {
  if (!host) return false;
  const name = hostnameOf(host);

  return name !== null && LOCAL_HOSTNAMES.has(name);
}

/**
 * Why a request must be refused, or null to let it through.
 *
 * The studio has no login, so the Host check stops DNS rebinding and the Origin
 * check stops other websites from posting to it. A request without an Origin
 * (curl, the MCP server's fetch) is not a browser cross-site request and passes.
 * With allowAnyHost, an operator who bound the studio to the LAN keeps the
 * Origin check but not the Host check.
 */
export function rejectReason(
  method: string,
  headers: { host?: string; origin?: string },
  allowAnyHost = false,
): string | null {
  if (!allowAnyHost && !isLocalHost(headers.host)) return "Forbidden host";

  if (SAFE_METHODS.has(method.toUpperCase()) || headers.origin === undefined) return null;
  let originHost: string;

  try {
    originHost = new URL(headers.origin).host;
  } catch {
    return "Forbidden origin";
  }

  return originHost.toLowerCase() === (headers.host ?? "").toLowerCase() ? null : "Forbidden origin";
}
