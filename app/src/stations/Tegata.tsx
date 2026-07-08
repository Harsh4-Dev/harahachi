// Tegata 手形: the travel-permit profile card with computed targets
// (GET/PUT /profile), the REQUIRED glass transparency slider writing
// --glass-alpha to :root (persisted in localStorage), and pairing status +
// re-pair.

import { useEffect, useState } from 'react';
import { createPortal } from 'react-dom';
import { useApp } from '../state/AppContext';
import { usePrefersReducedMotion } from '../App';
import { computeTargets, computeEtaDate } from '../lib/targets';
import { getGlassAlpha, isPushEnabled, setGlassAlpha, setPushEnabled } from '../api/storage';
import { fmtInt } from '../lib/format';
import { PetalLoader } from '../components/PetalLoader';
import { BodyPicker } from '../body/BodyPicker';
import { BodyPhotoCapture } from '../body/BodyPhotoCapture';
import { KeeperCard } from '../keeper/Keeper';
import { bodyFatLabel } from '../body/BodySilhouette';
import { enablePush, disablePush } from '../push/push';
import type { Activity, Goal, Profile, ProfileWithTargets, Sex } from '../api/types';

const SEX_LABEL: Record<Sex, string> = { m: 'Male', f: 'Female', x: 'Other' };
const ACTIVITY_LABEL: Record<Activity, string> = {
  sedentary: 'Sedentary',
  light: 'Light',
  moderate: 'Moderate',
  active: 'Active',
};
const GOAL_LABEL: Record<Goal, string> = { cut: 'Cut', maintain: 'Maintain', bulk: 'Bulk' };

export function Tegata() {
  const { client, isDemo, pairing, user, repair, logout, switchAccount, showError, toast, refreshMe } = useApp();
  const reduced = usePrefersReducedMotion();

  // Opening the permit re-checks is_admin (GET /auth/me), so a keeper promotion/
  // demotion flips the Checkpoint keeper card live without a re-login.
  useEffect(() => {
    void refreshMe();
  }, [refreshMe]);
  const [profile, setProfile] = useState<ProfileWithTargets | null>(null);
  const [editing, setEditing] = useState(false);
  const [glass, setGlass] = useState(() => getGlassAlpha());
  const [loadErr, setLoadErr] = useState(false);
  const [bump, setBump] = useState(0);

  useEffect(() => {
    let cancelled = false;
    setLoadErr(false);
    client
      .getProfile()
      .then((p) => !cancelled && setProfile(p))
      .catch(() => {
        if (!cancelled) {
          setProfile(null);
          setLoadErr(true);
        }
      });
    return () => {
      cancelled = true;
    };
  }, [client, bump]);

  const onGlass = (v: number) => {
    setGlass(v);
    setGlassAlpha(v); // writes --glass-alpha to :root + persists to localStorage
  };

  const save = async (draft: Profile) => {
    try {
      const updated = await client.putProfile(draft);
      setProfile(updated);
      setEditing(false);
      toast('Travel permit updated.', 'ok');
    } catch {
      showError({ message: 'Could not save your permit.', retry: () => void save(draft) });
    }
  };

  const targets = profile?.targets ?? (profile ? computeTargets(profile) : null);

  if (loadErr && !profile) {
    return (
      <div className="empty">
        <div style={{ marginBottom: 12 }}>Couldn’t load your permit.</div>
        <button className="btn btn-primary" onClick={() => setBump((b) => b + 1)}>
          Retry
        </button>
      </div>
    );
  }

  return (
    <div>
      {/* Account card (accounts v2) */}
      <div className="card glass">
        <div className="card-title">Account</div>
        <div className="row between" style={{ marginBottom: 12 }}>
          <div>
            <div className="small muted">Signed in as</div>
            <div className="display" style={{ fontSize: 20 }}>
              {user?.username ?? 'traveler'}
              {isDemo && <span className="small muted"> · demo</span>}
            </div>
          </div>
          <div className="permit-seal">{(user?.username ?? 'T').slice(0, 1).toUpperCase()}</div>
        </div>
        <div className="row" style={{ gap: 10 }}>
          <button className="btn btn-ghost" style={{ flex: 1 }} onClick={() => void logout()}>
            Log out
          </button>
          <button className="btn btn-ghost" style={{ flex: 1 }} onClick={switchAccount}>
            Switch account
          </button>
        </div>
      </div>

      {/* Checkpoint keeper (admin only — the card self-hides for non-keepers) */}
      <KeeperCard />

      {editing && profile ? (
        <PermitForm initial={profile} onSave={save} onCancel={() => setEditing(false)} />
      ) : profile ? (
        <div className="permit glass">
          <div className="permit-head">
            <div>
              <div className="small muted">Travel permit · 手形</div>
              <div className="display" style={{ fontSize: 24 }}>
                {profile.name || 'Traveler'}
              </div>
            </div>
            <div className="permit-seal">{(profile.name || 'T').slice(0, 1).toUpperCase()}</div>
          </div>

          <div className="permit-grid">
            <Cell k="Age" v={`${profile.age}`} />
            <Cell k="Sex" v={SEX_LABEL[profile.sex]} />
            <Cell k="Height" v={`${profile.height_cm} cm`} />
            <Cell k="Weight" v={`${profile.weight_kg} kg`} />
            <Cell k="Activity" v={ACTIVITY_LABEL[profile.activity]} />
            <Cell k="Goal" v={GOAL_LABEL[profile.goal]} />
          </div>

          {targets && (
            <>
              <div className="section-label" style={{ marginTop: 16 }}>
                Daily targets
              </div>
              <div className="permit-grid">
                <Cell k="Calories" v={`${fmtInt(targets.calories)}`} />
                <Cell k="Protein" v={`${targets.protein_g} g`} />
                <Cell k="Carbs" v={`${targets.carbs_g} g`} />
                <Cell k="Fat" v={`${targets.fat_g} g`} />
              </div>
            </>
          )}

          <button className="btn btn-ghost" style={{ width: '100%', marginTop: 14 }} onClick={() => setEditing(true)}>
            Edit permit
          </button>
        </div>
      ) : (
        <PetalLoader block caption="Loading permit…" />
      )}

      {/* Body composition (editable body-fat diagram + photo path) */}
      {profile && <BodyCompositionCard profile={profile} onSaved={(p) => setProfile(p)} reduced={reduced} />}

      {/* Goal weight + pace tiles + journey ETA (v3) */}
      {profile && <WeightGoalCard profile={profile} onSaved={(p) => setProfile(p)} />}

      {/* Push reminders toggle */}
      <PushCard />

      {/* Carry your records — CSV export (v3) */}
      <ExportCard />


      {/* REQUIRED glass transparency slider (DESIGN.md §2) */}
      <div className="card glass">
        <div className="card-title">Glass transparency</div>
        <div className="small muted" style={{ marginBottom: 10 }}>
          Adjust how frosted the panels feel. Applies everywhere, remembered on this phone.
        </div>
        <div className="row" style={{ gap: 12 }}>
          <span className="small muted">clear</span>
          <input
            type="range"
            min={0.15}
            max={0.9}
            step={0.01}
            value={glass}
            onChange={(e) => onGlass(parseFloat(e.target.value))}
            aria-label="Glass transparency"
          />
          <span className="small muted">solid</span>
        </div>
        <div className="center small muted tnum" style={{ marginTop: 6 }}>
          {Math.round(glass * 100)}%
        </div>
      </div>

      {/* Connection: status only. Server URLs and device ids are plumbing —
          an admin concern, never shown to the traveler. Re-pairing stays
          available from the login/pairing screens for setup. */}
      <div className="card glass">
        <div className="card-title">Connection</div>
        {isDemo ? (
          <div className="small muted">
            Running in <strong>demo mode</strong> with sample data.
            <button className="btn btn-primary" style={{ width: '100%', marginTop: 12 }} onClick={repair}>
              Connect to a checkpoint
            </button>
          </div>
        ) : pairing ? (
          <div className="small muted">
            <span style={{ color: 'var(--matcha)' }}>●</span> Connected to your checkpoint
          </div>
        ) : (
          <div className="small muted">
            Not connected.
            <button className="btn btn-primary" style={{ width: '100%', marginTop: 12 }} onClick={repair}>
              Connect
            </button>
          </div>
        )}
      </div>
    </div>
  );
}

function Cell({ k, v }: { k: string; v: string }) {
  return (
    <div>
      <div className="k">{k}</div>
      <div className="v">{v}</div>
    </div>
  );
}

// Editable body-fat diagram + photo path. Saving via PUT /profile recomputes
// targets server-side; we show the new numbers returned.
function BodyCompositionCard({
  profile,
  onSaved,
  reduced,
}: {
  profile: ProfileWithTargets;
  onSaved: (p: ProfileWithTargets) => void;
  reduced: boolean;
}) {
  const { client, showError, toast } = useApp();
  const [now, setNow] = useState<number | null>(profile.body_fat_pct ?? null);
  const [goal, setGoal] = useState<number | null>(profile.body_fat_goal_pct ?? null);
  const [photo, setPhoto] = useState(false);
  const [busy, setBusy] = useState(false);

  const dirty = (now ?? null) !== (profile.body_fat_pct ?? null) || (goal ?? null) !== (profile.body_fat_goal_pct ?? null);

  const save = async (nextNow: number | null, nextGoal: number | null) => {
    setBusy(true);
    const draft: Profile = {
      name: profile.name,
      age: profile.age,
      sex: profile.sex,
      height_cm: profile.height_cm,
      weight_kg: profile.weight_kg,
      activity: profile.activity,
      goal: profile.goal,
      body_fat_pct: nextNow,
      body_fat_goal_pct: nextGoal,
    };
    try {
      const updated = await client.putProfile(draft);
      onSaved(updated);
      toast(`Body composition saved — new target ${fmtInt(updated.targets.calories)} kcal.`, 'ok');
    } catch {
      showError({ message: 'Could not save body composition.', retry: () => void save(nextNow, nextGoal) });
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="card glass">
      <div className="card-title">Body composition</div>
      <div className="small muted" style={{ marginBottom: 10 }}>
        {profile.body_fat_pct != null
          ? `Now ${bodyFatLabel(profile.body_fat_pct)}${profile.body_fat_goal_pct != null ? ` · goal ${bodyFatLabel(profile.body_fat_goal_pct)}` : ''}. Targets use Katch-McArdle.`
          : 'Set your body fat to sharpen your targets (switches to Katch-McArdle).'}
      </div>
      <BodyPicker sex={profile.sex} now={now} goal={goal} onNow={setNow} onGoal={setGoal} onPhoto={() => setPhoto(true)} />
      {dirty && (
        <button
          className="btn btn-primary"
          style={{ width: '100%', marginTop: 10 }}
          onClick={() => void save(now, goal)}
          disabled={busy}
        >
          {busy ? 'Saving…' : 'Save body composition'}
        </button>
      )}
      {photo &&
        createPortal(
          <BodyPhotoCapture
            reducedMotion={reduced}
            onClose={() => setPhoto(false)}
            onConfirm={(pct) => {
              setNow(pct);
              setPhoto(false);
              void save(pct, goal);
            }}
          />,
          document.body,
        )}
    </div>
  );
}

// Push reminders toggle (API.md v2). Enabling subscribes via the SW PushManager;
// when push is unavailable (APK webview / denied) it explains and falls back to
// in-app banners.
function PushCard() {
  const { client, toast } = useApp();
  const [on, setOn] = useState(() => isPushEnabled());
  const [busy, setBusy] = useState(false);

  const toggle = async () => {
    setBusy(true);
    try {
      if (!on) {
        const res = await enablePush(client);
        if (res.ok) {
          setPushEnabled(true);
          setOn(true);
          toast('Reminders on — you’ll get a nudge at 6pm and 10pm.', 'ok');
        } else {
          // Not fatal: fall back to in-app banners, explain why.
          setPushEnabled(false);
          setOn(false);
          toast(res.message, 'info');
        }
      } else {
        await disablePush(client);
        setPushEnabled(false);
        setOn(false);
        toast('Reminders off.', 'info');
      }
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="card glass">
      <div className="card-title">Reminders</div>
      <div className="small muted" style={{ marginBottom: 10 }}>
        A gentle nudge at 6pm (what’s left) and 10pm (day recap), plus water checks through the day.
        In the Android app (APK) these arrive as in-app banners while Harahachi is open — web push isn’t
        available inside the WebView, so the banner is the mechanism there.
      </div>
      <button className="btn btn-primary" style={{ width: '100%' }} onClick={() => void toggle()} disabled={busy}>
        {busy ? 'Working…' : on ? 'Turn reminders off' : 'Turn reminders on'}
      </button>
    </div>
  );
}

// Goal weight + weekly pace tiles (v3). Pace drives the calorie deficit/surplus
// (7700 kcal ≈ 1 kg) — saving recomputes targets server-side. The journey ETA
// copy uses eta_date (falls back to a local computation for demo/offline).
const PACES = [0.25, 0.5, 0.75];

function WeightGoalCard({
  profile,
  onSaved,
}: {
  profile: ProfileWithTargets;
  onSaved: (p: ProfileWithTargets) => void;
}) {
  const { client, showError, toast } = useApp();
  const [goal, setGoal] = useState<number>(profile.weight_goal_kg ?? Math.round(profile.weight_kg));
  const [pace, setPace] = useState<number | null>(profile.pace_kg_per_week ?? null);
  const [busy, setBusy] = useState(false);

  const dirty =
    (profile.weight_goal_kg ?? null) !== goal || (profile.pace_kg_per_week ?? null) !== (pace ?? null);

  const draft: Profile = {
    ...profile,
    weight_goal_kg: goal,
    pace_kg_per_week: pace,
  };
  // Prefer the server's eta_date; fall back to a local computation so demo/
  // offline still show a date.
  const eta = profile.eta_date ?? computeEtaDate(draft);
  const preview = computeTargets(draft);

  const etaCopy = (() => {
    if (pace == null) return 'Pick a pace to see when you arrive.';
    if (eta == null) return `You're already at ${goal} kg — hold the line.`;
    const d = new Date(eta + 'T00:00:00');
    const near = d.toLocaleDateString('en-US', { month: 'long' });
    return `At this pace you arrive at ${goal} kg near the end of ${near} (${eta}).`;
  })();

  const save = async () => {
    setBusy(true);
    try {
      const updated = await client.putProfile(draft);
      onSaved(updated);
      toast(`Goal set — new target ${fmtInt(updated.targets.calories)} kcal.`, 'ok');
    } catch {
      showError({ message: 'Could not save your goal.', retry: () => void save() });
    } finally {
      setBusy(false);
    }
  };

  const stepGoal = (delta: number) => setGoal((g) => Math.min(300, Math.max(30, Math.round((g + delta) * 10) / 10)));

  return (
    <div className="card glass">
      <div className="card-title">Goal weight &amp; pace</div>

      <div className="field">
        <label>Goal weight (kg)</label>
        <div className="stepper">
          <button className="stepper-btn" onClick={() => stepGoal(-0.5)} aria-label="Decrease goal weight">
            −
          </button>
          <div className="stepper-val tnum">{goal.toFixed(1)}</div>
          <button className="stepper-btn" onClick={() => stepGoal(0.5)} aria-label="Increase goal weight">
            +
          </button>
        </div>
      </div>

      <div className="field">
        <label>Weekly pace</label>
        <div className="pill-group">
          {PACES.map((p) => (
            <button
              key={p}
              className={`pill ${pace === p ? 'active' : ''}`}
              onClick={() => setPace(p)}
            >
              {p} kg/wk
            </button>
          ))}
        </div>
      </div>

      <div className="glass-ink" style={{ padding: '10px 12px', borderRadius: 12, margin: '4px 0 12px', fontSize: 13, lineHeight: 1.45 }}>
        {etaCopy}
        {pace != null && (
          <>
            {' '}
            <span className="small">New target</span>{' '}
            <strong className="tnum">{fmtInt(preview.calories)} kcal</strong>
          </>
        )}
      </div>

      {dirty && (
        <button className="btn btn-primary" style={{ width: '100%' }} onClick={() => void save()} disabled={busy}>
          {busy ? 'Saving…' : 'Save goal & pace'}
        </button>
      )}
    </div>
  );
}

// "Carry your records with you" — CSV export (v3). Fetches GET /export.csv with
// the auth header (client.exportCsv → Blob) and triggers a browser download.
function ExportCard() {
  const { client, toast } = useApp();
  const [busy, setBusy] = useState(false);

  const download = async () => {
    setBusy(true);
    try {
      const blob = await client.exportCsv();
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `harahachi-records-${new Date().toISOString().slice(0, 10)}.csv`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
      toast('Records exported — check your downloads.', 'ok');
    } catch {
      toast('Could not export your records.', 'warn');
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="card glass">
      <div className="card-title">Carry your records</div>
      <div className="small muted" style={{ marginBottom: 10 }}>
        Take your whole journey with you — meals, workouts, weights, and water as a CSV.
      </div>
      <button className="btn btn-ghost" style={{ width: '100%' }} onClick={() => void download()} disabled={busy}>
        {busy ? 'Preparing…' : 'Export records (CSV)'}
      </button>
    </div>
  );
}

function PermitForm({
  initial,
  onSave,
  onCancel,
}: {
  initial: Profile;
  onSave: (p: Profile) => void;
  onCancel: () => void;
}) {
  const [name, setName] = useState(initial.name);
  const [age, setAge] = useState(initial.age);
  const [sex, setSex] = useState<Sex>(initial.sex);
  const [height, setHeight] = useState(initial.height_cm);
  const [weight, setWeight] = useState(initial.weight_kg);
  const [activity, setActivity] = useState<Activity>(initial.activity);
  const [goal, setGoal] = useState<Goal>(initial.goal);

  const draft: Profile = {
    name: name.trim(),
    age,
    sex,
    height_cm: height,
    weight_kg: weight,
    activity,
    goal,
  };
  const preview = computeTargets(draft);

  return (
    <div className="card glass">
      <div className="card-title">Edit permit</div>

      <div className="field">
        <label>Name</label>
        <input className="input" value={name} onChange={(e) => setName(e.target.value)} />
      </div>

      <div className="field">
        <label>Sex</label>
        <div className="pill-group">
          {(['m', 'f', 'x'] as Sex[]).map((s) => (
            <button key={s} className={`pill ${sex === s ? 'active' : ''}`} onClick={() => setSex(s)}>
              {SEX_LABEL[s]}
            </button>
          ))}
        </div>
      </div>

      <NumberStepper label="Age" value={age} min={10} max={100} step={1} onChange={setAge} />
      <NumberStepper label="Height (cm)" value={height} min={120} max={220} step={1} onChange={setHeight} />
      <NumberStepper
        label="Weight (kg)"
        value={weight}
        min={30}
        max={200}
        step={1}
        onChange={(v) => setWeight(v)}
      />

      <div className="field">
        <label>Activity</label>
        <div className="pill-group" style={{ flexWrap: 'wrap' }}>
          {(['sedentary', 'light', 'moderate', 'active'] as Activity[]).map((a) => (
            <button
              key={a}
              className={`pill ${activity === a ? 'active' : ''}`}
              style={{ flex: '1 0 44%' }}
              onClick={() => setActivity(a)}
            >
              {ACTIVITY_LABEL[a]}
            </button>
          ))}
        </div>
      </div>

      <div className="field">
        <label>Goal</label>
        <div className="pill-group">
          {(['cut', 'maintain', 'bulk'] as Goal[]).map((g) => (
            <button key={g} className={`pill ${goal === g ? 'active' : ''}`} onClick={() => setGoal(g)}>
              {GOAL_LABEL[g]}
            </button>
          ))}
        </div>
      </div>

      <div className="glass-ink" style={{ padding: '10px 12px', borderRadius: 12, margin: '4px 0 12px' }}>
        <span className="small">New target</span>{' '}
        <strong className="tnum">{fmtInt(preview.calories)} kcal</strong>{' '}
        <span className="small">· P{preview.protein_g} C{preview.carbs_g} F{preview.fat_g}</span>
      </div>

      <div className="row between">
        <button className="btn btn-ghost" onClick={onCancel}>
          Cancel
        </button>
        <button className="btn btn-primary" onClick={() => onSave(draft)} disabled={!name.trim()}>
          Save permit
        </button>
      </div>
    </div>
  );
}

function NumberStepper({
  label,
  value,
  min,
  max,
  step,
  onChange,
}: {
  label: string;
  value: number;
  min: number;
  max: number;
  step: number;
  onChange: (v: number) => void;
}) {
  const clamp = (v: number) => Math.min(max, Math.max(min, +v.toFixed(1)));
  return (
    <div className="field">
      <label>{label}</label>
      <div className="stepper">
        <button className="stepper-btn" onClick={() => onChange(clamp(value - step))} aria-label={`Decrease ${label}`}>
          −
        </button>
        <div className="stepper-val tnum">{value}</div>
        <button className="stepper-btn" onClick={() => onChange(clamp(value + step))} aria-label={`Increase ${label}`}>
          +
        </button>
      </div>
    </div>
  );
}
