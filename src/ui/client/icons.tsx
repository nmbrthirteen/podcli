import React from "react";

function Svg({ children, size = 15 }: { children: React.ReactNode; size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor"
      strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" style={{ display: "block" }}>
      {children}
    </svg>
  );
}

export const PlayIcon = () => <Svg><path d="M7 4.5v15l12-7.5z" fill="currentColor" stroke="none" /></Svg>;
export const PauseIcon = () => <Svg><path d="M8 4v16M16 4v16" /></Svg>;
export const FrameBackIcon = () => <Svg><path d="M15 6l-6 6 6 6" /></Svg>;
export const FrameForwardIcon = () => <Svg><path d="M9 6l6 6-6 6" /></Svg>;
export const CutBackIcon = () => <Svg><path d="M18 5v14l-9-7z" fill="currentColor" stroke="none" /><path d="M6 5v14" /></Svg>;
export const CutForwardIcon = () => <Svg><path d="M6 5v14l9-7z" fill="currentColor" stroke="none" /><path d="M18 5v14" /></Svg>;
export const CloseIcon = () => <Svg><path d="M6 6l12 12M18 6L6 18" /></Svg>;
export const TrashIcon = ({ size = 14 }: { size?: number }) => <Svg size={size}><path d="M4 7h16M10 11v6M14 11v6M5 7l1 13h12l1-13M9 7V4h6v3" /></Svg>;
