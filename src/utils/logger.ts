import winston from "winston";

const level = process.env.PODCLI_LOG_LEVEL ?? (process.env.NODE_ENV === "production" ? "info" : "debug");

/**
 * Shared structured logger for the MCP server, handlers, and web UI.
 *
 * Writes to stderr so stdout stays clean for MCP stdio transport.
 * Use child loggers (logger.child({ mod: "suggest-clips" })) per module.
 */
export const logger = winston.createLogger({
  level,
  format: winston.format.combine(
    winston.format.timestamp(),
    winston.format.errors({ stack: true }),
    winston.format.splat(),
    process.env.PODCLI_LOG_JSON === "1"
      ? winston.format.json()
      : winston.format.printf(({ timestamp, level: lvl, message, mod, ...rest }) => {
          const tag = mod ? ` [${mod as string}]` : "";
          const extra = Object.keys(rest).length ? ` ${JSON.stringify(rest)}` : "";
          return `${timestamp as string} ${lvl}${tag} ${message as string}${extra}`;
        }),
  ),
  transports: [new winston.transports.Console({ stderrLevels: ["error", "warn", "info", "debug"] })],
});

export function childLogger(mod: string): winston.Logger {
  return logger.child({ mod });
}
