// App-wide context: the active API client, pairing + account state, offline
// queue flush, a toast bus, and a shared error surface.
//
// v2 additions:
//  • Accounts: an active-user layer on top of device pairing. Phases now include
//    'login' (paired but logged out). register/login/logout/switch flows clear
//    volatile caches so a NEW account never sees the previous account's data.
//  • Global no_user (401) handling → routes to the login screen.
//  • A shared error surface: showError(message, retry?) drives a glass popup with
//    a RETRY button (App renders it). NO spinner may persist past its timeout —
//    callers that fail surface here instead of hanging.

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from 'react';
import type { HarahachiClient, Meal, MealInput, User } from '../api/types';
import { createDemoClient } from '../api/demo';
import { createHttpClient, ApiError, setNoUserHandler } from '../api/http';
import {
  clearActiveUser,
  clearDraftProfile,
  clearPairing,
  clearVolatileCaches,
  enqueueMeal,
  getActiveUser,
  getDraftProfile,
  getPairing,
  getQueue,
  isDemoEnabled,
  setActiveUser,
  setDemoEnabled,
  setOnboarded,
  setPairing as persistPairing,
  setQueue,
  type Pairing,
} from '../api/storage';

// 'login' = the device is paired (or demo) but there is no active account.
export type Phase = 'onboarding' | 'pairing' | 'login' | 'ready';

interface Toast {
  id: number;
  message: string;
  kind: 'info' | 'ok' | 'warn';
}

// A recoverable failure surfaced to the user with an optional retry action.
export interface ErrorSurface {
  message: string;
  retry?: () => void;
  // A secondary path ("enter manually", "re-scan"…) shown next to Retry.
  secondaryLabel?: string;
  onSecondary?: () => void;
}

interface AppState {
  client: HarahachiClient;
  isDemo: boolean;
  pairing: Pairing | null;
  user: User | null;
  phase: Phase;
  queueCount: number;
  toasts: Toast[];
  error: ErrorSurface | null;
  // logMeal queues locally on network failure and returns a provisional meal.
  logMeal(meal: MealInput): Promise<Meal>;
  finishOnboarding(): void;
  startDemo(): void;
  finishPairing(p: Pairing): void;
  goToPairing(): void;
  repair(): void;
  // Accounts.
  onRegistered(u: User): void;
  onLoggedIn(u: User): void;
  // Re-fetch GET /auth/me and refresh the active user (esp. is_admin) live, so
  // the Checkpoint keeper card appears/disappears without a re-login. Best-effort:
  // offline / no-user leaves the current user untouched.
  refreshMe(): Promise<void>;
  logout(): Promise<void>;
  switchAccount(): void;
  toast(message: string, kind?: Toast['kind']): void;
  dismissToast(id: number): void;
  showError(surface: ErrorSurface): void;
  clearError(): void;
}

const Ctx = createContext<AppState | null>(null);

function pickClient(pairing: Pairing | null, demo: boolean): HarahachiClient {
  if (demo) return createDemoClient();
  if (pairing) return createHttpClient(pairing);
  return createDemoClient(); // harmless default; UI is gated behind phase anyway
}

export function AppProvider({
  initialPhase,
  children,
}: {
  initialPhase: Phase;
  children: ReactNode;
}) {
  const [demo, setDemo] = useState<boolean>(isDemoEnabled());
  const [pairing, setPairingState] = useState<Pairing | null>(getPairing());
  const [user, setUser] = useState<User | null>(getActiveUser());
  const [phase, setPhase] = useState<Phase>(initialPhase);
  const [toasts, setToasts] = useState<Toast[]>([]);
  const [error, setError] = useState<ErrorSurface | null>(null);
  const [queueCount, setQueueCount] = useState<number>(getQueue().length);
  const toastId = useRef(1);

  const client = useMemo(() => pickClient(pairing, demo), [pairing, demo]);

  const dismissToast = useCallback((id: number) => {
    setToasts((t) => t.filter((x) => x.id !== id));
  }, []);

  const toast = useCallback(
    (message: string, kind: Toast['kind'] = 'info') => {
      const id = toastId.current++;
      setToasts((t) => [...t, { id, message, kind }]);
      window.setTimeout(() => dismissToast(id), 3200);
    },
    [dismissToast],
  );

  const showError = useCallback((surface: ErrorSurface) => setError(surface), []);
  const clearError = useCallback(() => setError(null), []);

  // Global no_user (401) → the device lost its active user; route to login and
  // drop volatile caches so nothing from the old session lingers.
  useEffect(() => {
    setNoUserHandler(() => {
      clearActiveUser();
      clearVolatileCaches();
      setUser(null);
      setDemo(false);
      setDemoEnabled(false);
      setPhase((p) => (p === 'onboarding' || p === 'pairing' ? p : 'login'));
    });
    return () => setNoUserHandler(null);
  }, []);

  const flushQueue = useCallback(async () => {
    const q = getQueue();
    if (q.length === 0 || demo) return;
    const remaining = [...q];
    let flushed = 0;
    for (const item of q) {
      try {
        await client.logMeal(item.meal);
        remaining.shift();
        flushed++;
      } catch {
        break; // still offline; stop and keep the rest queued
      }
    }
    setQueue(remaining);
    setQueueCount(remaining.length);
    if (flushed > 0) toast(`Synced ${flushed} queued meal${flushed > 1 ? 's' : ''}.`, 'ok');
  }, [client, demo, toast]);

  useEffect(() => {
    const onOnline = () => void flushQueue();
    window.addEventListener('online', onOnline);
    // Attempt a flush on mount too (app may have reopened online).
    void flushQueue();
    return () => window.removeEventListener('online', onOnline);
  }, [flushQueue]);

  const logMeal = useCallback(
    async (meal: MealInput): Promise<Meal> => {
      try {
        const stored = await client.logMeal(meal);
        return stored;
      } catch (e) {
        // Network/timeout failure → queue locally, return a provisional meal.
        const isNetwork = e instanceof ApiError && e.status === 0;
        if (!isNetwork) throw e;
        const queued = enqueueMeal(meal);
        setQueueCount(getQueue().length);
        toast('Offline — meal saved, will sync when Sekisho is reachable.', 'warn');
        return {
          id: queued.tempId,
          eaten_at: meal.eaten_at ?? new Date().toISOString(),
          ...meal,
        };
      }
    },
    [client, toast],
  );

  const finishOnboarding = useCallback(() => {
    setOnboarded(true);
    setPhase('pairing');
  }, []);

  const startDemo = useCallback(() => {
    setDemoEnabled(true);
    setDemo(true);
    setOnboarded(true);
    // Demo gets a canned account so the account UI is exercisable. It's a keeper
    // (admin) so the Checkpoint keeper surface is demoable straight from "try demo"
    // (matches the demo client's me()/login/register, which all set is_admin).
    const demoUser: User = { id: 'demo-user', username: 'traveler', is_admin: true };
    setActiveUser(demoUser);
    setUser(demoUser);
    // Carry any onboarding-collected profile into the demo session.
    const draft = getDraftProfile();
    if (draft) {
      void createDemoClient()
        .putProfile(draft)
        .catch(() => {});
      clearDraftProfile();
    }
    setPhase('ready');
    toast('Demo mode — exploring with sample data.', 'info');
  }, [toast]);

  const finishPairing = useCallback((p: Pairing) => {
    persistPairing(p);
    setPairingState(p);
    setDemoEnabled(false);
    setDemo(false);
    setOnboarded(true);
    // After pairing, the account is created on the account step (which follows).
    // If the device already has an active user (re-pair), me() will confirm it;
    // otherwise the account-creation step runs next. Route to account creation.
    setPhase('login');
  }, []);

  const goToPairing = useCallback(() => setPhase('pairing'), []);

  const repair = useCallback(() => {
    clearPairing();
    clearActiveUser();
    clearVolatileCaches();
    setPairingState(null);
    setUser(null);
    setDemoEnabled(false);
    setDemo(false);
    setPhase('pairing');
  }, []);

  // ---- Accounts ----

  // Called after a successful register (onboarding account step). The account is
  // brand-new → wipe any leftover volatile caches before entering the map.
  const onRegistered = useCallback((u: User) => {
    clearVolatileCaches();
    setActiveUser(u);
    setUser(u);
    setPhase('ready');
  }, []);

  // Called after a successful login. Clear volatile caches so the incoming
  // account never sees the previous account's cached day data.
  const onLoggedIn = useCallback((u: User) => {
    clearVolatileCaches();
    setActiveUser(u);
    setUser(u);
    setPhase('ready');
  }, []);

  // Live is_admin refresh. Only meaningful when there's an active account; the
  // response's is_admin can flip the keeper card on/off without re-login.
  const refreshMe = useCallback(async () => {
    if (phase !== 'ready') return;
    try {
      const res = await client.me();
      setUser((prev) => {
        // Guard against a stale response after an account switch.
        if (!prev || prev.id !== res.user.id) {
          if (!prev) return prev; // logged out meanwhile — ignore
        }
        if (
          prev &&
          prev.username === res.user.username &&
          !!prev.is_admin === !!res.user.is_admin &&
          prev.id === res.user.id
        ) {
          return prev; // unchanged — avoid a needless re-render
        }
        setActiveUser(res.user);
        return res.user;
      });
    } catch {
      /* offline / no_user (handled globally) — keep the current user */
    }
  }, [client, phase]);

  // Refresh is_admin on entering the map and whenever the app resumes.
  useEffect(() => {
    if (phase !== 'ready') return;
    void refreshMe();
    const onVis = () => {
      if (document.visibilityState === 'visible') void refreshMe();
    };
    document.addEventListener('visibilitychange', onVis);
    window.addEventListener('focus', refreshMe);
    return () => {
      document.removeEventListener('visibilitychange', onVis);
      window.removeEventListener('focus', refreshMe);
    };
  }, [phase, refreshMe]);

  const logout = useCallback(async () => {
    try {
      await client.logout();
    } catch {
      /* logging out locally regardless of server reachability */
    }
    clearActiveUser();
    clearVolatileCaches();
    setUser(null);
    // Demo logout returns to login too (a fresh demo account can be re-created).
    setPhase('login');
  }, [client]);

  // Switch account = log out then land on login to sign in as someone else.
  const switchAccount = useCallback(() => {
    void logout();
  }, [logout]);

  // Memoize the context value so consumers only re-render when a value they use
  // actually changes. Without this, the value object is a fresh identity on every
  // provider render (e.g. each transient toast), needlessly re-rendering every
  // useApp() consumer in the tree. All callbacks below are useCallback-stable.
  const value: AppState = useMemo(
    () => ({
      client,
      isDemo: demo,
      pairing,
      user,
      phase,
      queueCount,
      toasts,
      error,
      logMeal,
      finishOnboarding,
      startDemo,
      finishPairing,
      goToPairing,
      repair,
      onRegistered,
      onLoggedIn,
      refreshMe,
      logout,
      switchAccount,
      toast,
      dismissToast,
      showError,
      clearError,
    }),
    [
      client,
      demo,
      pairing,
      user,
      phase,
      queueCount,
      toasts,
      error,
      logMeal,
      finishOnboarding,
      startDemo,
      finishPairing,
      goToPairing,
      repair,
      onRegistered,
      onLoggedIn,
      refreshMe,
      logout,
      switchAccount,
      toast,
      dismissToast,
      showError,
      clearError,
    ],
  );

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useApp(): AppState {
  const v = useContext(Ctx);
  if (!v) throw new Error('useApp must be used within AppProvider');
  return v;
}
