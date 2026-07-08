// Checkpoint keeper (関) — the admin surface. Revealed on the Tegata permit only
// when the signed-in user is_admin (the demo account is a keeper). A glass card
// opens a full-height glass overlay (pattern-matched to the Annai chat overlay),
// with glass-pill tabs:
//   • Pair     — mint a pairing invite for another traveler's phone (QR + token +
//                live countdown + regenerate).
//   • Overview — fleet of travelers (today kcal vs target mini journey bar,
//                streak, last activity; tap → drill-down), paired devices
//                (revoke with confirm), backend mode, push subs, public URL.
//   • Backends — the vision/text backend registry; tap a backend to make it
//                active (confirm toast).
//
// Every keeper call is device-key authed and timed out in the client. Load
// failures show an inline retry; mutating actions surface the shared error popup
// with a Retry. A non-keeper device (403 {code:"not_keeper"}) is toasted.

import { useCallback, useEffect, useState } from 'react';
import { createPortal } from 'react-dom';
import { useApp } from '../state/AppContext';
import { ApiError } from '../api/http';
import { useSwipeDown } from '../lib/useSwipeDown';
import { fmtInt, fmtTime } from '../lib/format';
import type {
  BackendRole,
  KeeperBackend,
  KeeperDevice,
  KeeperOverview,
  KeeperPairResponse,
  KeeperUserDetail,
  KeeperUserRow,
} from '../api/types';

type Tab = 'pair' | 'overview' | 'backends';

// ---- small helpers ----

function mmss(total: number): string {
  const s = Math.max(0, Math.floor(total));
  const m = Math.floor(s / 60);
  return `${m}:${String(s % 60).padStart(2, '0')}`;
}

function relTime(iso: string | null): string {
  if (!iso) return 'never';
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return '';
  const diff = Date.now() - then;
  const m = Math.round(diff / 60000);
  if (m < 1) return 'just now';
  if (m < 60) return `${m}m ago`;
  const h = Math.round(m / 60);
  if (h < 24) return `${h}h ago`;
  return `${Math.round(h / 24)}d ago`;
}

// A shared error router: not_keeper → toast; everything else → the shared error
// popup with a Retry (user requirement: no dead-end failures anywhere).
function useKeeperError(): (e: unknown, message: string, retry?: () => void) => void {
  const { showError, toast } = useApp();
  return useCallback(
    (e: unknown, message: string, retry?: () => void) => {
      if (e instanceof ApiError && e.code === 'not_keeper') {
        toast('You are not a checkpoint keeper.', 'warn');
        return;
      }
      showError({ message, retry });
    },
    [showError, toast],
  );
}

// ---- Entry card (rendered inside Tegata) ----

export function KeeperCard() {
  const { user } = useApp();
  const [open, setOpen] = useState(false);
  // Non-admins never see the card. The server still enforces every call.
  if (!user?.is_admin) return null;
  return (
    <>
      <button className="card glass keeper-card" onClick={() => setOpen(true)}>
        <span className="keeper-badge">関</span>
        <span className="keeper-card-text">
          <span className="card-title" style={{ marginBottom: 2 }}>
            Checkpoint keeper
          </span>
          <span className="small muted">Invite travelers, watch the road, switch backends.</span>
        </span>
        <span className="keeper-chevron" aria-hidden>
          ›
        </span>
      </button>
      {open && createPortal(<KeeperOverlay onClose={() => setOpen(false)} />, document.body)}
    </>
  );
}

// ---- Full-height overlay ----

function KeeperOverlay({ onClose }: { onClose: () => void }) {
  const [tab, setTab] = useState<Tab>('overview');
  const sheetRef = useSwipeDown<HTMLDivElement>(onClose);
  return (
    <div className="keeper-overlay" role="dialog" aria-label="Checkpoint keeper">
      <div className="keeper-sheet glass" ref={sheetRef}>
        <div className="keeper-head" data-swipe-handle>
          <div className="keeper-head-title">
            <span className="keeper-avatar">関</span>
            <div>
              <div className="display" style={{ fontSize: 18 }}>
                Checkpoint keeper
              </div>
              <div className="small muted">関所番 · admin</div>
            </div>
          </div>
          <button className="capture-x" aria-label="Close keeper" onClick={onClose}>
            ✕
          </button>
        </div>

        <div className="keeper-tabs" role="tablist">
          <TabButton id="pair" tab={tab} setTab={setTab} label="Pair" />
          <TabButton id="overview" tab={tab} setTab={setTab} label="Overview" />
          <TabButton id="backends" tab={tab} setTab={setTab} label="Backends" />
        </div>

        <div className="keeper-scroll" data-swipe-scroll>
          {tab === 'pair' && <PairTab />}
          {tab === 'overview' && <OverviewTab />}
          {tab === 'backends' && <BackendsTab />}
        </div>
      </div>
    </div>
  );
}

function TabButton({
  id,
  tab,
  setTab,
  label,
}: {
  id: Tab;
  tab: Tab;
  setTab: (t: Tab) => void;
  label: string;
}) {
  return (
    <button
      role="tab"
      aria-selected={tab === id}
      className={`keeper-tab ${tab === id ? 'active' : ''}`}
      onClick={() => setTab(id)}
    >
      {label}
    </button>
  );
}

// Inline retry block for a failed load (matches Tegata's load-error pattern).
function LoadError({ onRetry }: { onRetry: () => void }) {
  return (
    <div className="empty">
      <div style={{ marginBottom: 12 }}>Couldn’t reach the checkpoint.</div>
      <button className="btn btn-primary" onClick={onRetry}>
        Retry
      </button>
    </div>
  );
}

// ---- (1) Pair ----

function PairTab() {
  const { client } = useApp();
  const onErr = useKeeperError();
  const [data, setData] = useState<KeeperPairResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState(false);
  const [left, setLeft] = useState(0);

  const load = useCallback(() => {
    setLoading(true);
    setErr(false);
    let cancelled = false;
    client
      .keeperPair()
      .then((r) => {
        if (cancelled) return;
        setData(r);
        setLeft(r.expires_in);
      })
      .catch((e) => {
        if (cancelled) return;
        setErr(true);
        onErr(e, 'Could not mint a pairing invite.', load);
      })
      .finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
    };
  }, [client, onErr]);

  useEffect(() => load(), [load]);

  // Live countdown from expires_in.
  useEffect(() => {
    if (!data) return;
    const t = window.setInterval(() => setLeft((s) => (s > 0 ? s - 1 : 0)), 1000);
    return () => window.clearInterval(t);
  }, [data]);

  if (loading && !data) return <div className="empty">Minting an invite…</div>;
  if (err && !data) return <LoadError onRetry={load} />;
  if (!data) return null;

  const expired = left <= 0;

  return (
    <div className="keeper-pair">
      <div className="small muted center" style={{ marginBottom: 12 }}>
        Have the new traveler open the app and scan this at the pairing screen.
      </div>

      <div className="keeper-qr-card">
        <img
          className="keeper-qr"
          src={`data:image/png;base64,${data.qr_png_base64}`}
          alt="Pairing QR code"
          width={200}
          height={200}
        />
      </div>

      <div className="keeper-token-label small muted">Pairing token</div>
      <div className="keeper-token tnum">{data.token}</div>

      <div className={`keeper-countdown ${expired ? 'expired' : ''}`}>
        {expired ? 'Expired — mint a fresh invite' : `Expires in ${mmss(left)}`}
      </div>

      <button className="btn btn-primary" style={{ width: '100%', marginTop: 12 }} onClick={load} disabled={loading}>
        {loading ? 'Minting…' : expired ? 'Mint a fresh invite' : 'Regenerate'}
      </button>
    </div>
  );
}

// ---- (2) Overview ----

function OverviewTab() {
  const { client } = useApp();
  const onErr = useKeeperError();
  const [data, setData] = useState<KeeperOverview | null>(null);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState(false);
  const [drill, setDrill] = useState<KeeperUserRow | null>(null);

  const load = useCallback(() => {
    setLoading(true);
    setErr(false);
    let cancelled = false;
    client
      .keeperOverview()
      .then((r) => !cancelled && setData(r))
      .catch((e) => {
        if (cancelled) return;
        setErr(true);
        onErr(e, 'Could not load the road overview.', load);
      })
      .finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
    };
  }, [client, onErr]);

  useEffect(() => load(), [load]);

  if (loading && !data) return <div className="empty">Reading the road…</div>;
  if (err && !data) return <LoadError onRetry={load} />;
  if (!data) return null;

  if (drill) return <UserDetail row={drill} onBack={() => setDrill(null)} />;

  return (
    <div>
      {/* Travelers */}
      <div className="section-label">Travelers</div>
      <div className="keeper-list">
        {data.users.map((u) => (
          <button key={u.id} className="keeper-user-row glass" onClick={() => setDrill(u)}>
            <div className="row between">
              <div className="keeper-user-name">{u.username}</div>
              <div className="small muted">{relTime(u.last_activity)}</div>
            </div>
            <MiniJourney today={u.today_kcal} target={u.target_kcal} />
            <div className="row between small muted" style={{ marginTop: 4 }}>
              <span className="tnum">
                {fmtInt(u.today_kcal)} / {fmtInt(u.target_kcal)} kcal
              </span>
              <span>{u.streak > 0 ? `${u.streak}日 streak` : 'no streak'}</span>
            </div>
          </button>
        ))}
      </div>

      {/* Devices */}
      <div className="section-label" style={{ marginTop: 18 }}>
        Devices
      </div>
      {data.devices.length === 0 ? (
        <div className="small muted">No paired devices.</div>
      ) : (
        <div className="keeper-list">
          {data.devices.map((d) => (
            <DeviceRow key={d.id} device={d} onRevoked={load} />
          ))}
        </div>
      )}

      {/* Station status */}
      <div className="section-label" style={{ marginTop: 18 }}>
        Checkpoint
      </div>
      <div className="keeper-chips">
        <span className="keeper-chip">backend · {data.backend_mode}</span>
        <span className="keeper-chip">{data.push_subs} push subs</span>
      </div>
      {/* Public URL — the keeper context is the one place server URLs are shown. */}
      <div className="keeper-url-label small muted" style={{ marginTop: 12 }}>
        Public URL
      </div>
      <div className="keeper-url tnum">{data.public_url}</div>
    </div>
  );
}

function MiniJourney({ today, target }: { today: number; target: number }) {
  const frac = target > 0 ? today / target : 0;
  const pct = Math.min(100, Math.max(0, frac * 100));
  const over = frac > 1;
  const overPct = over ? Math.min(100, (frac - 1) * 100) : 0;
  return (
    <div className="kp-mini" aria-hidden>
      <div className="kp-mini-fill" style={{ width: `${pct}%` }} />
      {over && <div className="kp-mini-over" style={{ width: `${overPct}%` }} />}
    </div>
  );
}

function DeviceRow({ device, onRevoked }: { device: KeeperDevice; onRevoked: () => void }) {
  const { client, toast } = useApp();
  const onErr = useKeeperError();
  const [confirm, setConfirm] = useState(false);
  const [busy, setBusy] = useState(false);

  const revoke = async () => {
    setBusy(true);
    try {
      await client.keeperRevokeDevice(device.id);
      toast(`Revoked ${device.name}.`, 'ok');
      onRevoked();
    } catch (e) {
      setBusy(false);
      setConfirm(false);
      onErr(e, `Could not revoke ${device.name}.`, () => void revoke());
    }
  };

  return (
    <div className="keeper-device-row glass">
      <div className="row between">
        <div>
          <div className="keeper-user-name">{device.name}</div>
          <div className="small muted">
            {device.active_user ? `${device.active_user} · ` : ''}
            {relTime(device.last_seen)}
          </div>
        </div>
        {confirm ? (
          <div className="row" style={{ gap: 8 }}>
            <button className="btn btn-ghost btn-sm" onClick={() => setConfirm(false)} disabled={busy}>
              Cancel
            </button>
            <button className="btn btn-danger btn-sm" onClick={() => void revoke()} disabled={busy}>
              {busy ? '…' : 'Revoke'}
            </button>
          </div>
        ) : (
          <button className="btn btn-ghost btn-sm" onClick={() => setConfirm(true)}>
            Revoke
          </button>
        )}
      </div>
    </div>
  );
}

function UserDetail({ row, onBack }: { row: KeeperUserRow; onBack: () => void }) {
  const { client } = useApp();
  const onErr = useKeeperError();
  const [detail, setDetail] = useState<KeeperUserDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState(false);

  const load = useCallback(() => {
    setLoading(true);
    setErr(false);
    let cancelled = false;
    client
      .keeperUser(row.id)
      .then((r) => !cancelled && setDetail(r))
      .catch((e) => {
        if (cancelled) return;
        setErr(true);
        onErr(e, `Could not load ${row.username}.`, load);
      })
      .finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
    };
  }, [client, onErr, row.id, row.username]);

  useEffect(() => load(), [load]);

  return (
    <div className="keeper-drill">
      <button className="keeper-back" onClick={onBack}>
        ‹ back to travelers
      </button>
      <div className="display" style={{ fontSize: 22, margin: '6px 0 2px' }}>
        {row.username}
      </div>

      {loading && !detail ? (
        <div className="empty">Loading traveler…</div>
      ) : err && !detail ? (
        <LoadError onRetry={load} />
      ) : detail ? (
        <>
          <div className="permit-grid" style={{ marginTop: 10 }}>
            <DetailCell k="Age" v={`${detail.profile.age}`} />
            <DetailCell k="Weight" v={`${detail.profile.weight_kg} kg`} />
            <DetailCell k="Goal" v={detail.profile.goal} />
            <DetailCell k="Target" v={`${fmtInt(detail.profile.targets.calories)} kcal`} />
          </div>

          <div className="section-label" style={{ marginTop: 16 }}>
            Recent meals
          </div>
          {detail.recent_meals.length === 0 ? (
            <div className="small muted">No meals logged yet.</div>
          ) : (
            <div className="keeper-list">
              {detail.recent_meals.map((m) => (
                <div key={m.id} className="keeper-meal-row glass">
                  <div className="row between">
                    <div className="keeper-user-name">{m.dish}</div>
                    <div className="small muted tnum">{fmtInt(m.calories)} kcal</div>
                  </div>
                  <div className="small muted">{fmtTime(m.eaten_at)}</div>
                </div>
              ))}
            </div>
          )}
        </>
      ) : null}
    </div>
  );
}

function DetailCell({ k, v }: { k: string; v: string }) {
  return (
    <div>
      <div className="k">{k}</div>
      <div className="v">{v}</div>
    </div>
  );
}

// ---- (3) Backends ----

const ROLE_LABEL: Record<BackendRole, string> = { vision: 'Vision', text: 'Text' };

function BackendsTab() {
  const { client, toast } = useApp();
  const onErr = useKeeperError();
  const [backends, setBackends] = useState<KeeperBackend[] | null>(null);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState(false);
  const [busyId, setBusyId] = useState<string | null>(null);

  const load = useCallback(() => {
    setLoading(true);
    setErr(false);
    let cancelled = false;
    client
      .keeperBackends()
      .then((r) => !cancelled && setBackends(r.backends))
      .catch((e) => {
        if (cancelled) return;
        setErr(true);
        onErr(e, 'Could not load the backend registry.', load);
      })
      .finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
    };
  }, [client, onErr]);

  useEffect(() => load(), [load]);

  const activate = async (b: KeeperBackend) => {
    if (b.active) return;
    setBusyId(b.id);
    try {
      await client.keeperSetBackend(b.id, b.role);
      toast(`${ROLE_LABEL[b.role]} backend → ${b.name}.`, 'ok');
      load();
    } catch (e) {
      onErr(e, `Could not switch the ${ROLE_LABEL[b.role].toLowerCase()} backend.`, () => void activate(b));
    } finally {
      setBusyId(null);
    }
  };

  if (loading && !backends) return <div className="empty">Reading backends…</div>;
  if (err && !backends) return <LoadError onRetry={load} />;
  if (!backends) return null;

  const roles: BackendRole[] = ['vision', 'text'];

  return (
    <div>
      {roles.map((role) => {
        const rows = backends.filter((b) => b.role === role);
        if (rows.length === 0) return null;
        return (
          <div key={role}>
            <div className="section-label" style={{ marginTop: role === 'vision' ? 0 : 18 }}>
              {ROLE_LABEL[role]} backend
            </div>
            <div className="keeper-list">
              {rows.map((b) => (
                <button
                  key={b.id}
                  className={`keeper-backend-row glass ${b.active ? 'active' : ''}`}
                  onClick={() => void activate(b)}
                  disabled={b.active || busyId === b.id}
                >
                  <div>
                    <div className="keeper-user-name">{b.name}</div>
                    <div className="small muted">{b.kind}</div>
                  </div>
                  {b.active ? (
                    <span className="keeper-active-mark">● active</span>
                  ) : busyId === b.id ? (
                    <span className="small muted">switching…</span>
                  ) : (
                    <span className="small muted">tap to use</span>
                  )}
                </button>
              ))}
            </div>
          </div>
        );
      })}
    </div>
  );
}
