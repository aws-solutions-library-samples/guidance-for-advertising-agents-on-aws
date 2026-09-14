/**
 * The chat view, moved out of `App` when the journey view became the default route.
 *
 * Behaviour is unchanged: agent selector, session viewer, replay banner, tool log and admin panel all
 * work exactly as before. Only its location changed, from "what `App` renders once signed in" to "what
 * the `/chat` route renders". Nothing here is new, and that is deliberate: the journey view was added
 * beside this, not on top of it.
 */

import { useMemo, useState } from 'react';

import { useAppConfig } from '../hooks/useAppConfig';
import { useConversation } from '../hooks/useConversation';
import { useInvoke } from '../hooks/useInvoke';
import { useSessionViewer } from '../hooks/useSessionViewer';
import { useSessionsList } from '../hooks/useSessionsList';
import { useUserAdmin } from '../hooks/useUserAdmin';
import type { AuthState } from '../hooks/useAuth';
import type { ChatTarget } from '../lib/a2a';
import { describeBuild } from '../lib/config';
import { initialConversation } from '../lib/conversation';
import type { AppConfig } from '../lib/types';
import { AdminPanel } from './AdminPanel';
import { AppShell } from './AppShell';
import { ChatColumn } from './ChatColumn';
import { SessionSelect } from './SessionSelect';
import { SessionsPanel } from './SessionsPanel';
import { TestBriefs } from './TestBriefs';
import { ToolLog } from './ToolLog';
import { ViewerBanner } from './ViewerBanner';

export interface ChatViewProps {
  readonly config: AppConfig | null;
  readonly auth: AuthState;
}

export function ChatView({ config, auth }: ChatViewProps) {
  const [adminOpen, setAdminOpen] = useState(false);
  const [agentId, setAgentId] = useState<string | null>(null);

  // Which agent turns go to. The registry's default until the reader picks another, and null when
  // the registry is empty, which the composer reports rather than silently doing nothing.
  const target: ChatTarget | null = useMemo(() => {
    if (!config) return null;
    const agents = config.agents as ChatTarget[];
    const chosen = agentId ?? config.default_agent_id;
    return agents.find((a) => a.id === chosen) ?? agents[0] ?? null;
  }, [config, agentId]);

  const { conversation, toolLog, send, toolActivityNote, startNewSession, resume } = useConversation(
    config,
    auth.session,
    target,
  );

  const invoke = useInvoke(config, auth.session);
  const signedIn = auth.session !== null;
  const sessionsList = useSessionsList(invoke, signedIn);
  const viewer = useSessionViewer(invoke);
  const [sessionsPanelOpen, setSessionsPanelOpen] = useState(false);
  const userAdmin = useUserAdmin(invoke);

  // Whether the chat column is showing a recorded session rather than the reader's own. The two
  // transcripts are simply two pieces of state, so nothing has to be detached and restored: the
  // live conversation is still there, untouched, when the reader comes back to it.
  const replaying = viewer.viewedSessionId !== null;

  if (!auth.session) return null;

  return (
    <>
      <AppShell
        username={auth.session.username}
        endpointLabel={endpointLabel(config)}
        buildLabel={describeBuild(config?.ui_published_at)}
        isAdmin={auth.isAdmin}
        onSignOut={auth.signOut}
        onOpenAdmin={() => setAdminOpen(true)}
        headerControls={
          <>
            {config && config.agents.length > 0 && (
              <select
                value={target?.id ?? ''}
                onChange={(e) => setAgentId(e.target.value)}
                title="Agent to chat with"
                // Disabled while replaying: the selection would apply to the reader's own
                // conversation, which is not what is on screen.
                disabled={replaying}
                className="max-w-[220px] rounded-full border border-line bg-surface px-3 py-1.5 text-[13px] text-ink-2 disabled:opacity-50"
              >
                {config.agents.map((agent) => (
                  <option key={agent.id} value={agent.id}>
                    {agent.name}
                  </option>
                ))}
              </select>
            )}
            <SessionSelect
              sessions={sessionsList.sessions}
              viewedSessionId={viewer.viewedSessionId}
              onView={viewer.view}
              onStop={viewer.stop}
              onOpenPanel={() => setSessionsPanelOpen(true)}
            />
            <button
              type="button"
              onClick={startNewSession}
              // Disabled while replaying for the same reason the agent selector is: the reader is
              // looking at somebody else's recorded session, and this acts on their own.
              disabled={replaying}
              data-testid="chat-new-session-button"
              title="Clear this conversation and start a new session id"
              className="rounded-full border border-line bg-surface px-3 py-1.5 text-[13px] text-ink-2 hover:border-sky hover:text-ink disabled:opacity-50"
            >
              New session
            </button>
          </>
        }
        chat={
          <>
            {replaying && viewer.viewedSessionId !== null && (
              <ViewerBanner
                sessionId={viewer.viewedSessionId}
                meta={viewer.meta}
                onExit={viewer.stop}
                onResume={() => {
                  // Seed the live conversation with the recorded bubbles and adopt its id, then
                  // leave replay so the (now-seeded) live view is on screen and can take a turn.
                  resume(viewer.viewedSessionId!, viewer.messages);
                  viewer.stop();
                }}
              />
            )}
            <ChatColumn
              conversation={
                replaying ? { ...initialConversation, messages: viewer.messages } : conversation
              }
              onSend={send}
              {...(replaying
                ? {
                    readOnlyNote:
                      viewer.staleNote ??
                      viewer.status ??
                      'This is a read-only replay. Return to your own session to send a turn.',
                  }
                : target === null
                  ? {
                      readOnlyNote:
                        config?.agents_error !== undefined && config.agents_error !== ''
                          ? `No agents are available: ${config.agents_error}`
                          : 'No chat agents are configured, so there is nothing to send a turn to.',
                    }
                  : {})}
              emptyState={
                replaying ? (
                  <p className="m-0 text-[13px] text-muted">
                    {viewer.status ?? 'No steps recorded for this session.'}
                  </p>
                ) : (
                  <EmptyState
                    agentName={target?.name ?? null}
                    onPickBrief={send}
                    {...(target === null
                      ? { briefsDisabledReason: 'No chat agents are configured, so there is nowhere to send a brief.' }
                      : {})}
                  />
                )
              }
            />
          </>
        }
        log={
          <ToolLog
            calls={replaying ? viewer.toolLog.calls : toolLog.calls}
            note={replaying ? null : toolActivityNote}
          />
        }
      />
      {adminOpen && auth.isAdmin && (
        <AdminPanel
          admin={userAdmin}
          clientId={config?.cognito_client_id ?? null}
          onClose={() => setAdminOpen(false)}
        />
      )}
      {sessionsPanelOpen && (
        <SessionsPanel
          sessions={sessionsList.sessions}
          error={sessionsList.error}
          viewedSessionId={viewer.viewedSessionId}
          onView={viewer.view}
          onClose={() => setSessionsPanelOpen(false)}
        />
      )}
      {adminOpen && (
        <Placeholder onDismiss={() => setAdminOpen(false)}>
          User administration is not ported yet.
        </Placeholder>
      )}
    </>
  );
}

/** What the header says about where turns are sent. Says "not configured" when it is not. */
function endpointLabel(config: ReturnType<typeof useAppConfig>['config']): string {
  if (!config) return 'configuration unavailable';
  if (!config.agent_runtime_arn) return 'no runtime configured';
  const name = config.agent_runtime_arn.split('/').pop() ?? config.agent_runtime_arn;
  return `${name} · ${config.aws_region}`;
}

/**
 * What the chat column shows before the first turn.
 *
 * States plainly that the traffic is real, because that is the point of the tool log beside it and
 * is worth saying rather than leaving the reader to assume either way.
 */
function EmptyState({
  agentName,
  onPickBrief,
  briefsDisabledReason,
}: {
  agentName: string | null;
  onPickBrief: (prompt: string) => void;
  briefsDisabledReason?: string;
}) {
  return (
    <div className="mx-auto max-w-[560px] pt-8">
      <h2 className="m-0 text-[19px] font-bold">Ask the agent to find inventory</h2>
      <p className="mt-2 mb-0 text-[13.5px] leading-relaxed text-muted">
        {agentName !== null
          ? `Turns go to ${agentName}, which queries every sales agent it is configured with and `
          : 'The agent queries every sales agent it is configured with and '}
        decides which inventory fits. Nothing here is simulated: the calls and results on the right
        are the real request and response traffic.
      </p>
      <p className="mt-4 mb-0 text-[13px] text-muted">
        Type a brief below, or start from one of these:
      </p>
      <TestBriefs
        onPick={onPickBrief}
        {...(briefsDisabledReason !== undefined ? { disabledReason: briefsDisabledReason } : {})}
      />
    </div>
  );
}

/**
 * Marks a part of the UI that is not built yet.
 *
 * Says so plainly instead of rendering a mock. A placeholder that looks like the finished thing
 * reports progress that does not exist.
 */
function Placeholder({
  children,
  onDismiss,
}: {
  children: React.ReactNode;
  onDismiss?: () => void;
}) {
  return (
    <div className="scroll-column flex-1 p-4">
      <p className="m-0 rounded-lg border border-dashed border-line px-3 py-2 text-[13px] text-muted">
        {children}
        {onDismiss && (
          <button
            type="button"
            onClick={onDismiss}
            className="ml-2 font-semibold text-sky underline"
          >
            Close
          </button>
        )}
      </p>
    </div>
  );
}
