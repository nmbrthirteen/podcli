// Scripts with no case distinction whose letters Unicode nonetheless assigns
// an uppercase mapping for display styling (Georgian Mkhedruli -> Mtavruli).
// String.prototype.toUpperCase() applies that mapping uncritically, so a
// caption style that uppercases for emphasis silently switches alphabets for
// these scripts instead of just emphasizing them.
const CASELESS_SCRIPT_RANGES: Array<[number, number]> = [
  [0x10a0, 0x10ff], // Georgian (Mkhedruli, Asomtavruli)
  [0x1c90, 0x1cbf], // Georgian Extended (Mtavruli)
  [0x2d00, 0x2d2f], // Georgian Supplement
];

function isCaselessScriptChar(ch: string): boolean {
  const cp = ch.codePointAt(0) ?? 0;
  return CASELESS_SCRIPT_RANGES.some(([lo, hi]) => cp >= lo && cp <= hi);
}

/**
 * Uppercase text, except for scripts where Unicode's uppercase mapping
 * would change the alphabet rather than just the case (see above).
 */
export function safeUpper(text: string): string {
  if (!text) return text;
  let hasCaseless = false;
  for (const ch of text) {
    if (isCaselessScriptChar(ch)) {
      hasCaseless = true;
      break;
    }
  }
  if (!hasCaseless) return text.toUpperCase();
  return Array.from(text)
    .map((ch) => (isCaselessScriptChar(ch) ? ch : ch.toUpperCase()))
    .join("");
}
