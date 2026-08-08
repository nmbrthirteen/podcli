import React, { useEffect, useState } from "react";

type Account = {
  signedIn: boolean;
  workspace?: string;
  plan?: string;
  episodesUsed?: number;
  cap?: number;
};

/**
 * Signed-in state at the bottom of the sidebar.
 *
 * Shows nothing when signed out. Sync that runs invisibly feels like sync that
 * isn't running, so a subscriber should be able to see their workspace without
 * going looking for it.
 */
export default function AccountChip() {
  const [account, setAccount] = useState<Account | null>(null);

  useEffect(() => {
    fetch("/api/pro/account")
      .then((r) => r.json())
      .then(setAccount)
      .catch(() => setAccount({ signedIn: false }));
  }, []);

  if (!account?.signedIn) return null;

  const used = account.episodesUsed ?? 0;
  const cap = account.cap ?? 0;
  // Only surface the quota once it's close enough to matter. A counter at 2/10
  // is noise; at 8/10 it's the difference between planning and being surprised.
  const showQuota = cap > 0 && used / cap >= 0.7;

  return (
    <div className="sidebar-account">
      <div className="sidebar-account-name">{account.workspace}</div>
      <div className="sidebar-account-sub">
        {account.plan === "team" ? "Team" : "Pro"}
        {showQuota && ` · ${used}/${cap} episodes`}
      </div>
    </div>
  );
}
