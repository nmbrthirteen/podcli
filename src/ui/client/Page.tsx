import React from "react";

export function PageHeader({
  title,
  actions,
  back,
}: {
  title: React.ReactNode;
  actions?: React.ReactNode;
  back?: React.ReactNode;
}) {
  return (
    <div className="header">
      {back && <div className="page-back">{back}</div>}
      <div className="page-head">
        <h1>{title}</h1>
        {actions && <div className="page-head-actions">{actions}</div>}
      </div>
    </div>
  );
}
