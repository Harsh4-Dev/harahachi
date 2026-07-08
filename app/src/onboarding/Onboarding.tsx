// First-launch onboarding — the tegata (Edo travel permit) scroll flow.
// Steps: welcome/name → age/sex/height/weight → BODY DIAGRAM (body-fat now/goal)
// → activity + goal. It stashes the profile draft and moves to pairing; the
// account (username + PIN + hanko seal) is created after pairing (Account.tsx).
//
// CLEAN FIRST-OPEN (user requirement): every field starts pristine — name empty,
// no age/height/weight pre-selected. Numeric fields show neutral placeholders and
// only commit a value on touch; each step's Continue is disabled until its
// required values are set. Nothing is a "default answer" the user didn't choose.

import { useState } from 'react';
import { useApp } from '../state/AppContext';
import { usePrefersReducedMotion } from '../App';
import { setDraftProfile } from '../api/storage';
import { computeTargets } from '../lib/targets';
import { fmtInt } from '../lib/format';
import { BodyPicker } from '../body/BodyPicker';
import { BodyPhotoCapture } from '../body/BodyPhotoCapture';
import type { Activity, Goal, Profile, Sex } from '../api/types';

const SEX: { id: Sex; label: string }[] = [
  { id: 'm', label: 'Male' },
  { id: 'f', label: 'Female' },
  { id: 'x', label: 'Other' },
];
const ACTIVITY: { id: Activity; label: string; note: string }[] = [
  { id: 'sedentary', label: 'Sedentary', note: 'desk, little exercise' },
  { id: 'light', label: 'Light', note: '1–3 days/week' },
  { id: 'moderate', label: 'Moderate', note: '3–5 days/week' },
  { id: 'active', label: 'Active', note: '6–7 days/week' },
];
const GOAL: { id: Goal; label: string; note: string }[] = [
  { id: 'cut', label: 'Cut', note: 'lose weight' },
  { id: 'maintain', label: 'Maintain', note: 'stay steady' },
  { id: 'bulk', label: 'Bulk', note: 'gain weight' },
];

const STEPS = 4;

export function Onboarding() {
  const { finishOnboarding } = useApp();
  const reduced = usePrefersReducedMotion();
  const [step, setStep] = useState(0);

  // Pristine start: nothing pre-chosen. Numbers are null until touched.
  const [name, setName] = useState('');
  const [age, setAge] = useState<number | null>(null);
  const [sex, setSex] = useState<Sex | null>(null);
  const [height, setHeight] = useState<number | null>(null);
  const [weight, setWeight] = useState<number | null>(null);
  const [bfNow, setBfNow] = useState<number | null>(null);
  const [bfGoal, setBfGoal] = useState<number | null>(null);
  const [activity, setActivity] = useState<Activity | null>(null);
  const [goal, setGoal] = useState<Goal | null>(null);
  const [photo, setPhoto] = useState(false);

  // Build a profile only from what's been set; used for the live target preview.
  const draftProfile: Profile = {
    name: name.trim() || 'Traveler',
    age: age ?? 25,
    sex: sex ?? 'x',
    height_cm: height ?? 170,
    weight_kg: weight ?? 65,
    activity: activity ?? 'moderate',
    goal: goal ?? 'maintain',
    body_fat_pct: bfNow,
    body_fat_goal_pct: bfGoal,
  };
  const targets = computeTargets(draftProfile);

  // Per-step gating.
  const canContinue =
    step === 0
      ? name.trim().length > 0
      : step === 1
        ? sex !== null && age !== null && height !== null && weight !== null
        : step === 2
          ? true // body diagram is optional (can skip / do later in Tegata)
          : activity !== null && goal !== null;

  const next = () => setStep((s) => Math.min(STEPS - 1, s + 1));
  const back = () => setStep((s) => Math.max(0, s - 1));

  const finish = () => {
    // Persist only chosen values; unset ones fall back to neutral defaults so the
    // server always gets a valid profile, but nothing the user didn't touch is
    // shown to them as "their answer" earlier in the flow.
    const profile: Profile = {
      name: name.trim() || 'Traveler',
      age: age ?? 25,
      sex: sex ?? 'x',
      height_cm: height ?? 170,
      weight_kg: weight ?? 65,
      activity: activity ?? 'moderate',
      goal: goal ?? 'maintain',
      body_fat_pct: bfNow,
      body_fat_goal_pct: bfGoal,
    };
    setDraftProfile(profile);
    finishOnboarding();
  };

  if (photo) {
    return (
      <BodyPhotoCapture
        reducedMotion={reduced}
        onClose={() => setPhoto(false)}
        onConfirm={(pct) => {
          setBfNow(pct);
          setPhoto(false);
        }}
      />
    );
  }

  return (
    <div className="scroll-flow">
      <div className="scroll-sheet glass">
        <div className="scroll-top" aria-hidden />
        <div className="scroll-dots">
          {Array.from({ length: STEPS }).map((_, i) => (
            <span key={i} className={`dot ${i === step ? 'on' : ''}`} />
          ))}
        </div>

        <div className="scroll-body">
          {step === 0 && (
            <div className="ob-step">
              <div className="ob-kanji">手形</div>
              <h1 className="display ob-title">Welcome, traveler</h1>
              <p className="muted">
                Harahachi turns your day into a walk along the old sakura road. First, let's fill out your{' '}
                <em>tegata</em> — your travel permit.
              </p>
              <div className="field" style={{ marginTop: 18 }}>
                <label>What should we call you?</label>
                <input
                  className="input"
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  placeholder="Your name"
                  autoFocus
                />
              </div>
            </div>
          )}

          {step === 1 && (
            <div className="ob-step">
              <h1 className="display ob-title">A little about you</h1>
              <div className="field">
                <label>Sex</label>
                <div className="pill-group">
                  {SEX.map((s) => (
                    <button key={s.id} className={`pill ${sex === s.id ? 'active' : ''}`} onClick={() => setSex(s.id)}>
                      {s.label}
                    </button>
                  ))}
                </div>
              </div>
              <UnsetStepper label="Age" value={age} unit="yrs" fallback={25} min={10} max={100} step={1} onChange={setAge} />
              <UnsetSlider label="Height" value={height} unit="cm" fallback={170} min={120} max={220} onChange={setHeight} />
              <UnsetSlider label="Weight" value={weight} unit="kg" fallback={65} min={30} max={200} onChange={setWeight} />
            </div>
          )}

          {step === 2 && (
            <div className="ob-step">
              <h1 className="display ob-title">Your body</h1>
              <p className="muted">
                Pick the silhouette closest to you now and your goal. This sharpens your targets — you can skip and set it
                later in Tegata.
              </p>
              <BodyPicker
                sex={sex ?? 'x'}
                now={bfNow}
                goal={bfGoal}
                onNow={setBfNow}
                onGoal={setBfGoal}
                onPhoto={() => setPhoto(true)}
              />
            </div>
          )}

          {step === 3 && (
            <div className="ob-step">
              <h1 className="display ob-title">Your pace</h1>
              <div className="field">
                <label>How active are you?</label>
                <div className="choice-list">
                  {ACTIVITY.map((a) => (
                    <button
                      key={a.id}
                      className={`choice ${activity === a.id ? 'active' : ''}`}
                      onClick={() => setActivity(a.id)}
                    >
                      <span className="choice-label">{a.label}</span>
                      <span className="choice-note">{a.note}</span>
                    </button>
                  ))}
                </div>
              </div>
              <div className="field">
                <label>Your goal</label>
                <div className="choice-list">
                  {GOAL.map((g) => (
                    <button key={g.id} className={`choice ${goal === g.id ? 'active' : ''}`} onClick={() => setGoal(g.id)}>
                      <span className="choice-label">{g.label}</span>
                      <span className="choice-note">{g.note}</span>
                    </button>
                  ))}
                </div>
              </div>
              {activity !== null && goal !== null && (
                <div className="glass-ink target-preview">
                  <span className="small">Your daily walk</span>
                  <div className="tnum" style={{ fontSize: 22, fontWeight: 800 }}>
                    {fmtInt(targets.calories)} kcal
                  </div>
                  <span className="small">
                    P{targets.protein_g} · C{targets.carbs_g} · F{targets.fat_g} g
                  </span>
                </div>
              )}
            </div>
          )}
        </div>

        <div className="scroll-actions">
          {step > 0 ? (
            <button className="btn btn-ghost" onClick={back}>
              Back
            </button>
          ) : (
            <span />
          )}
          {step < STEPS - 1 ? (
            <button className="btn btn-primary" onClick={next} disabled={!canContinue}>
              {step === 2 && bfNow === null ? 'Skip for now' : 'Continue'}
            </button>
          ) : (
            <button className="btn btn-primary" onClick={finish} disabled={!canContinue}>
              To the checkpoint
            </button>
          )}
        </div>
        <div className="scroll-bottom" aria-hidden />
      </div>
    </div>
  );
}

// A stepper that shows a neutral placeholder until first touched (pristine start).
function UnsetStepper({
  label,
  value,
  unit,
  fallback,
  min,
  max,
  step,
  onChange,
}: {
  label: string;
  value: number | null;
  unit: string;
  fallback: number;
  min: number;
  max: number;
  step: number;
  onChange: (v: number) => void;
}) {
  const clamp = (v: number) => Math.min(max, Math.max(min, v));
  const current = value ?? fallback;
  return (
    <div className="field">
      <label>
        {label} <span className="muted">({unit})</span>
      </label>
      <div className="stepper" style={{ justifyContent: 'center' }}>
        <button className="stepper-btn" onClick={() => onChange(clamp(current - step))} aria-label={`Decrease ${label}`}>
          −
        </button>
        <div className={`stepper-val tnum ${value === null ? 'unset' : ''}`}>{value === null ? '—' : value}</div>
        <button className="stepper-btn" onClick={() => onChange(clamp(current + step))} aria-label={`Increase ${label}`}>
          +
        </button>
      </div>
    </div>
  );
}

// A slider whose handle sits at a neutral midpoint but reads "—" until touched.
function UnsetSlider({
  label,
  value,
  unit,
  fallback,
  min,
  max,
  onChange,
}: {
  label: string;
  value: number | null;
  unit: string;
  fallback: number;
  min: number;
  max: number;
  onChange: (v: number) => void;
}) {
  return (
    <div className="field">
      <label>
        {label} <span className="muted">({unit})</span>
      </label>
      <div className={`slider-readout tnum ${value === null ? 'unset' : ''}`}>
        {value === null ? `— ${unit}` : `${value} ${unit}`}
      </div>
      <input
        type="range"
        min={min}
        max={max}
        step={1}
        value={value ?? fallback}
        onChange={(e) => onChange(parseInt(e.target.value, 10))}
        aria-label={label}
      />
    </div>
  );
}
