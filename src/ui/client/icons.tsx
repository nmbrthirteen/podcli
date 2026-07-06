import React from "react";
import { Play, Pause, ChevronLeft, ChevronRight, SkipBack, SkipForward, X, Trash2, ArrowLeft, Download } from "lucide-react";

const block = { display: "block" } as const;

export const PlayIcon = ({ size = 15 }: { size?: number }) => <Play size={size} style={block} fill="currentColor" strokeWidth={0} />;
export const BackIcon = ({ size = 14 }: { size?: number }) => <ArrowLeft size={size} style={block} />;
export const PauseIcon = () => <Pause size={15} style={block} />;
export const FrameBackIcon = () => <ChevronLeft size={15} style={block} />;
export const FrameForwardIcon = () => <ChevronRight size={15} style={block} />;
export const CutBackIcon = () => <SkipBack size={15} style={block} fill="currentColor" />;
export const CutForwardIcon = () => <SkipForward size={15} style={block} fill="currentColor" />;
export const CloseIcon = () => <X size={15} style={block} />;
export const TrashIcon = ({ size = 14 }: { size?: number }) => <Trash2 size={size} style={block} />;
export const DownloadIcon = ({ size = 14 }: { size?: number }) => <Download size={size} style={block} />;
