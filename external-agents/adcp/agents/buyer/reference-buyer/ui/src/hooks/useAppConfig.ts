/**
 * Loads the app configuration once, and reports its loading state until it has.
 *
 * The three states are kept distinct rather than collapsed into "config or not": loading, failed
 * with a reason, and loaded. A failure that renders as an empty UI is indistinguishable from a UI
 * with nothing to show, and only one of those is worth telling someone about.
 */

import { useEffect, useState } from 'react';

import { fetchAppConfig } from '../lib/config';
import type { AppConfig } from '../lib/types';

export interface ConfigState {
  config: AppConfig | null;
  error: string | null;
  loading: boolean;
}

export function useAppConfig(): ConfigState {
  const [config, setConfig] = useState<AppConfig | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let active = true;
    fetchAppConfig()
      .then((loaded) => {
        if (!active) return;
        setConfig(loaded);
        setError(null);
      })
      .catch((err: unknown) => {
        if (!active) return;
        setError(err instanceof Error ? err.message : String(err));
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    // Stops a late response writing into an unmounted component, which is the same class of
    // problem as the overlapping-poll race in the vanilla session viewer.
    return () => {
      active = false;
    };
  }, []);

  return { config, error, loading };
}
