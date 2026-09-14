import { useMemo } from 'react';
import { Navigate, Route, Routes } from 'react-router-dom';

import { AgentAdmin } from './components/AgentAdmin';
import { ChatView } from './components/ChatView';
import { LoginScreen } from './components/LoginScreen';
import { JourneyView } from './components/journey/JourneyView';
import { useAppConfig } from './hooks/useAppConfig';
import { useAuth } from './hooks/useAuth';
import { useInvoke } from './hooks/useInvoke';
import type { CognitoConfig } from './lib/cognito';

/**
 * The gate, then the routes.
 *
 * The gate comes first and is unchanged: config loads before anything else, because the login screen
 * needs the pool's client id and the app needs the runtime ARN. A config failure is reported as
 * itself rather than as a sign-in problem, since "we could not read our own configuration" and "your
 * password was wrong" call for different responses.
 *
 * Past the gate there are two views. The journey view is the default because it is what a booth
 * screen should show without anyone driving it. The chat view moved to `/chat` and is otherwise
 * untouched.
 *
 * An unknown path renders the journey view rather than a 404: the deployed site serves `index.html`
 * for any unmatched path (a CloudFront rewrite), so a mistyped URL should land somewhere useful.
 */
export function App() {
  const { config, error: configError, loading: configLoading } = useAppConfig();

  const cognitoConfig: CognitoConfig | null = useMemo(
    () =>
      config && config.cognito_region && config.cognito_client_id
        ? { region: config.cognito_region, clientId: config.cognito_client_id }
        : null,
    [config],
  );

  const auth = useAuth(cognitoConfig);
  const invoke = useInvoke(config, auth.session);

  if (configLoading) {
    return <Centered>Loading configuration…</Centered>;
  }

  if (configError !== null) {
    return (
      <Centered>
        <span className="text-red">Could not load configuration: {configError}</span>
        <span className="mt-2 block text-[13px] text-muted">
          The UI reads <code>/config</code> when served by app.py and <code>./config.json</code>{' '}
          when served from S3. Neither answered.
        </span>
      </Centered>
    );
  }

  if (config && !cognitoConfig) {
    // Reported rather than shown as a login form that cannot succeed.
    return (
      <Centered>
        <span className="text-red">No Cognito user pool is configured.</span>
        <span className="mt-2 block text-[13px] text-muted">
          COGNITO_REGION and COGNITO_CLIENT_ID are missing from the server&apos;s configuration, so
          there is nothing to sign in to.
        </span>
      </Centered>
    );
  }

  if (!auth.session) {
    return <LoginScreen auth={auth} clientId={config?.cognito_client_id ?? null} />;
  }

  return (
    <Routes>
      <Route
        path="/"
        element={<JourneyView invoke={invoke} config={config} enabled={auth.session !== null} />}
      />
      <Route path="/chat" element={<ChatView config={config} auth={auth} />} />
      {/* Admin screen for editing the seller/governance registries. Gated on admin membership here
          for discoverability; the runtime re-checks it on every action, so the URL is not a bypass. */}
      <Route path="/admin/agents" element={<AgentAdmin invoke={invoke} isAdmin={auth.isAdmin} />} />
      {/* Preserved so an old bookmark still lands on the chat view rather than on the journey. */}
      <Route path="/legacy" element={<Navigate to="/chat" replace />} />
      <Route
        path="*"
        element={<JourneyView invoke={invoke} config={config} enabled={auth.session !== null} />}
      />
    </Routes>
  );
}

function Centered({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex h-full items-center justify-center p-8 text-center">
      <div className="max-w-[460px] text-[13.5px]">{children}</div>
    </div>
  );
}
