import { readFileSync } from "fs";
import { paths } from "../config/paths.js";
import type { UIState } from "../models/index.js";

/** Read the studio's persisted session state. Throws when the file is missing or unreadable. */
export function readUIStateFile(): UIState {
  // SAFETY: ui-state.json is written only by the studio's persistState(), which serializes a UIState.
  return JSON.parse(readFileSync(paths.uiState, "utf-8")) as UIState;
}
