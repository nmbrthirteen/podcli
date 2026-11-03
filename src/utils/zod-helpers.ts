import { z } from "zod";

/** Reads a null from an external process as an absent value, matching an optional TypeScript field. */
export const absentWhenNull = <T extends z.ZodType>(schema: T) =>
  schema.nullish().transform((v) => v ?? undefined);
