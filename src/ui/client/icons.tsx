import React from "react";
import { Play, Pause, ChevronLeft, ChevronRight, SkipBack, SkipForward, X, Trash2 } from "lucide-react";

const block = { display: "block" } as const;

export const PlayIcon = () => <Play size={15} style={block} fill="currentColor" strokeWidth={0} />;
export const PauseIcon = () => <Pause size={15} style={block} />;
export const FrameBackIcon = () => <ChevronLeft size={15} style={block} />;
export const FrameForwardIcon = () => <ChevronRight size={15} style={block} />;
export const CutBackIcon = () => <SkipBack size={15} style={block} fill="currentColor" />;
export const CutForwardIcon = () => <SkipForward size={15} style={block} fill="currentColor" />;
export const CloseIcon = () => <X size={15} style={block} />;
export const TrashIcon = ({ size = 14 }: { size?: number }) => <Trash2 size={size} style={block} />;
