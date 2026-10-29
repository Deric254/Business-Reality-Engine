import { useState } from "react";
import type { ConnectorRow } from "../api/client";
import { ConnectWizard, type Kind } from "./ConnectWizard";
import { MpesaFlow } from "./MpesaFlow";
import { SoftwareFlow } from "./SoftwareFlow";
import { Card, btnCls } from "./ui";

type Mode = "pick" | "software" | "mpesa" | "file" | "shop" | "more";

const CHOICES: { id: Exclude<Mode, "pick">; title: string; blurb: string }[] = [
  { id: "software", title: "My business software", blurb: "smePro, Imara or Pharmacy ERP on your computer." },
  { id: "mpesa", title: "M-Pesa payments", blurb: "See every customer payment as it happens." },
  { id: "file", title: "A spreadsheet or file", blurb: "Excel, CSV, Google Sheets or an M-Pesa statement." },
  { id: "shop", title: "My online shop", blurb: "WooCommerce or Shopify." },
  { id: "more", title: "Something else", blurb: "Other systems, a web link, email or a shared folder." },
];
const FILE: Kind[] = ["file", "sheets"];
const SHOP: Kind[] = ["woo", "shopify"];
const MORE: Kind[] = ["link", "webhook", "database", "sftp", "email", "folder", "agent"];

/** The one place to connect something. Five plain choices; each opens a short guided path. Nothing technical is shown until
 *  someone picks "Something else". */
export function ConnectHub({ tenantId, firstTime, startSystem, onClose, onChanged, onConnected, onTypeByHand }: {
  tenantId: string; firstTime: boolean; startSystem?: string; onClose: () => void; onChanged: () => void; onTypeByHand: () => void;
  onConnected: (created: ConnectorRow, summary: string | null) => void;
}) {
  const [mode, setMode] = useState<Mode>(startSystem ? "software" : "pick");
  const back = () => setMode("pick");
  if (mode === "software") return <SoftwareFlow tenantId={tenantId} start={startSystem} onClose={startSystem ? onClose : back} onChanged={onChanged} />;
  if (mode === "mpesa") return <MpesaFlow tenantId={tenantId} onClose={back} onChanged={onChanged} />;
  if (mode === "file") return <ConnectWizard tenantId={tenantId} kinds={FILE} heading="How do you want to bring it in?" onClose={back} onDone={onConnected} />;
  if (mode === "shop") return <ConnectWizard tenantId={tenantId} kinds={SHOP} heading="Which online shop?" onClose={back} onDone={onConnected} />;
  if (mode === "more") return <ConnectWizard tenantId={tenantId} kinds={MORE} heading="Other ways to connect" onClose={back} onDone={onConnected} />;
  return (
    <Card className="!p-6 space-y-5">
      <div className="flex items-start justify-between gap-3">
        <div>
          <h2 className="display text-2xl">{firstTime ? "Let's connect your business" : "Connect something else"}</h2>
          <p className="text-sm text-ink-muted mt-1 max-w-prose">Where do your sales and expenses come from? Pick the closest one. We'll guide you, step by step, and show you when it works.</p>
        </div>
        {!firstTime && <button className={btnCls} onClick={onClose}>Cancel</button>}
      </div>
      <div className="grid sm:grid-cols-2 lg:grid-cols-3 gap-3">
        {CHOICES.map((c) => (
          <button key={c.id} onClick={() => setMode(c.id)} className="text-left border border-border rounded-xl p-4 bg-panel hover:border-info hover:bg-info-soft/40 transition-colors">
            <div className="font-semibold">{c.title}</div>
            <div className="text-sm text-ink-muted mt-1">{c.blurb}</div>
          </button>
        ))}
      </div>
      <p className="text-sm text-ink-muted">No system at all? <button type="button" className="underline" onClick={onTypeByHand}>Type your sales and expenses by hand</button>.</p>
    </Card>
  );
}
