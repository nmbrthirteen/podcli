import { staticFile } from "remotion";
import type { ThemeInput } from "./theme";

export const DEFAULT_PACK_TEXTURES: NonNullable<ThemeInput["textures"]> = {
  paper: staticFile("style/detail-paper.png"),
  kraft: staticFile("style/detail-kraft.png"),
  tape: [
    staticFile("style/tape-1.png"),
    staticFile("style/tape-2.png"),
    staticFile("style/tape-3.png"),
  ],
};

export const withDefaultTextures = (theme: ThemeInput | null): ThemeInput | null => {
  if (!theme) return null;
  if (theme.textures && Object.keys(theme.textures).length > 0) return theme;
  return { ...theme, textures: DEFAULT_PACK_TEXTURES };
};
