/**
 * Agent administration screen: edit the resources that let the buyer reach sellers and governance
 * agents.
 *
 * This screen only manages connection registry entries (URL, transport, auth). It does not touch the
 * AdCP call/response path — a registration is just where the buyer looks up how to reach an agent
 * before making the same AdCP call it always makes. A change here takes effect on the next buyer
 * request, with no redeploy.
 *
 * Honesty rules this screen keeps: a token value is never shown (the server sends only whether one is
 * stored); environment built-ins are shown read-only, because they come from configuration and are
 * changed by redeploying; a connection test shows the real outcome, never a simulated one.
 */

import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';

import { useAgentAdmin, type AgentAdminState } from '../hooks/useAgentAdmin';
import type { InvokeFn } from '../hooks/useInvoke';
import {
  emptyForm,
  formFromRegistration,
  validateRegistrationForm,
  type AgentKind,
  type AgentRegistration,
  type FieldErrors,
  type RegistrationForm,
} from '../lib/agentRegistry';
import { Icon } from './Icon';

export interface AgentAdminProps {
  invoke: InvokeFn;
  isAdmin: boolean;
}

export function AgentAdmin({ invoke, isAdmin }: AgentAdminProps) {
  const sellers = useAgentAdmin(invoke, 'seller');
  const governance = useAgentAdmin(invoke, 'governance');

  useEffect(() => {
    if (!isAdmin) return;
    void sellers.refresh();
    void governance.refresh();
    // Loaded once when the screen opens.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isAdmin]);

  return (
    <div className="flex h-full flex-col">
      <header className="flex flex-none items-center gap-3 border-b border-line bg-surface px-5 py-2.5">
        <Icon name="network" size={18} color="var(--color-blue)" />
        <div className="min-w-0">
          <h1 className="m-0 text-[15px] font-bold leading-tight">Agent administration</h1>
          <div className="truncate text-[13px] text-muted">
            Sellers and governance agents the buyer can reach
          </div>
        </div>
        <Link
          to="/chat"
          data-testid="agent-admin-back"
          className="ml-auto rounded-full border border-line px-3 py-1.5 text-[13px] font-semibold text-ink-2 hover:bg-line-soft"
        >
          Back to chat
        </Link>
      </header>

      <div className="scroll-column flex-1 p-5">
        {!isAdmin ? (
          <p className="m-0 rounded-lg border border-dashed border-line px-3 py-2 text-[13px] text-muted">
            Agent administration requires membership of the admin group. Every action here is also
            enforced server-side, so this screen simply has nothing to offer a non-admin.
          </p>
        ) : (
          <div className="mx-auto flex max-w-[900px] flex-col gap-8">
            <AgentSection
              admin={sellers}
              kind="seller"
              title="Seller agents"
              description="Sales agents the buyer queries for inventory (get_products, create_media_buy). Adding one lets the buyer target it immediately, with no redeploy."
            />
            <AgentSection
              admin={governance}
              kind="governance"
              title="Governance agents"
              description="The campaign-governance agent the buyer consults before a buy (check_governance). AdCP allows one per account, so the buyer uses the first configured entry."
            />
          </div>
        )}
      </div>
    </div>
  );
}

function AgentSection({
  admin,
  kind,
  title,
  description,
}: {
  admin: AgentAdminState;
  kind: AgentKind;
  title: string;
  description: string;
}) {
  // null = no form open; otherwise the id being edited, or '' for a new entry.
  const [editingId, setEditingId] = useState<string | null>(null);
  const [form, setForm] = useState<RegistrationForm>(emptyForm());
  const [errors, setErrors] = useState<FieldErrors>({});
  const editingReg =
    editingId !== null && editingId !== ''
      ? admin.agents.find((a) => a.id === editingId) ?? null
      : null;

  function openNew() {
    setForm(emptyForm());
    setErrors({});
    setEditingId('');
  }
  function openEdit(reg: AgentRegistration) {
    setForm(formFromRegistration(reg));
    setErrors({});
    setEditingId(reg.id);
  }
  function closeForm() {
    setEditingId(null);
    setErrors({});
  }

  async function submit() {
    const result = validateRegistrationForm(
      kind,
      form,
      editingReg?.has_stored_token ?? false,
      editingReg?.has_stored_secret ?? false,
    );
    if (!result.ok || !result.entry) {
      setErrors(result.errors);
      return;
    }
    const ok = await admin.save(result.entry);
    if (ok) closeForm();
  }

  return (
    <section>
      <div className="flex items-center gap-3">
        <h2 className="m-0 text-[15px] font-bold">{title}</h2>
        <button
          type="button"
          onClick={openNew}
          disabled={admin.busy}
          data-testid={`${kind}-add-button`}
          className="ml-auto rounded-full border border-line bg-surface px-3 py-1.5 text-[13px] font-semibold text-ink-2 hover:border-sky hover:text-ink disabled:opacity-40"
        >
          Add {kind}
        </button>
      </div>
      <p className="mt-1 mb-3 text-[13px] leading-relaxed text-muted">{description}</p>

      {admin.error !== null && (
        <p className="mb-3 rounded-lg border border-line-soft bg-[#fde8e8] px-3 py-2 text-[13px] text-red">
          {admin.error}
        </p>
      )}

      {admin.testResult !== null && (
        <div
          className={`mb-3 flex items-start gap-2 rounded-lg border px-3 py-2 text-[13px] ${
            admin.testResult.ok
              ? 'border-line-soft bg-[#e3f8ee] text-green'
              : 'border-line-soft bg-[#fef3e2] text-orange'
          }`}
        >
          <Icon name={admin.testResult.ok ? 'check' : 'close'} size={14} />
          <span className="min-w-0">
            <strong>{admin.testResult.id}</strong>: {admin.testResult.message}
            {typeof admin.testResult.duration_ms === 'number' &&
              ` (${admin.testResult.duration_ms} ms)`}
          </span>
          <button
            type="button"
            onClick={admin.dismissTest}
            className="ml-auto font-semibold underline"
          >
            Dismiss
          </button>
        </div>
      )}

      {admin.loading ? (
        <p className="m-0 text-[13px] text-muted">Loading…</p>
      ) : admin.agents.length === 0 ? (
        <p className="m-0 text-[13px] text-muted">None configured.</p>
      ) : (
        <table className="w-full border-collapse text-[13px]">
          <thead>
            <tr className="text-left tracking-wide text-muted uppercase">
              <th className="py-2 pr-3 font-semibold">Name</th>
              <th className="py-2 pr-3 font-semibold">URL</th>
              <th className="py-2 pr-3 font-semibold">Transport</th>
              <th className="py-2 pr-3 font-semibold">Source</th>
              <th className="py-2 pr-3 font-semibold">Auth</th>
              <th className="py-2 font-semibold" />
            </tr>
          </thead>
          <tbody>
            {admin.agents.map((agent) => (
              <tr
                key={agent.id}
                className={`border-t border-line-soft align-top ${agent.hidden ? 'opacity-45' : ''}`}
              >
                <td className="py-2 pr-3">
                  <div className="font-semibold">{agent.name}</div>
                  <div className="font-mono text-muted">{agent.id}</div>
                </td>
                <td className="py-2 pr-3 font-mono break-all text-muted">{agent.url}</td>
                <td className="py-2 pr-3 uppercase">{agent.transport}</td>
                <td className="py-2 pr-3">
                  <div className="flex flex-wrap gap-1">
                    <Pill
                      className={
                        agent.source === 'stored'
                          ? 'bg-[#f3eeff] text-purple'
                          : 'bg-line-soft text-muted'
                      }
                    >
                      {agent.source === 'stored' ? 'custom' : 'built-in'}
                    </Pill>
                    {agent.hidden ? <Pill className="bg-[#fde8e8] text-red">removed</Pill> : null}
                  </div>
                </td>
                <td className="py-2 pr-3">
                  {agent.auth_type === 'cognito_bearer'
                    ? 'in-account'
                    : agent.auth_type === 'm2m_oauth'
                      ? agent.has_stored_secret
                        ? 'client credentials'
                        : 'client creds (env)'
                      : agent.has_stored_token
                        ? 'token stored'
                        : agent.source === 'env'
                          ? 'from env'
                          : 'token'}
                </td>
                <td className="py-2">
                  <div className="flex justify-end gap-2">
                    {/* Testing a removed agent would fail: it is no longer in the effective registry
                        the resolver reads, so the test is hidden until it is restored. */}
                    {!agent.hidden && (
                      <button
                        type="button"
                        onClick={() => void admin.test(agent.id)}
                        disabled={admin.testingId === agent.id}
                        data-testid={`${kind}-test-${agent.id}`}
                        className="rounded-full border border-line px-2.5 py-1 font-semibold text-ink-2 hover:bg-line-soft disabled:opacity-40"
                      >
                        {admin.testingId === agent.id ? 'Testing…' : 'Test'}
                      </button>
                    )}
                    {agent.editable ? (
                      <>
                        <button
                          type="button"
                          onClick={() => openEdit(agent)}
                          data-testid={`${kind}-edit-${agent.id}`}
                          className="rounded-full border border-line px-2.5 py-1 font-semibold text-ink-2 hover:bg-line-soft"
                        >
                          Edit
                        </button>
                        <button
                          type="button"
                          onClick={() => void admin.remove(agent.id)}
                          disabled={admin.busy}
                          data-testid={`${kind}-delete-${agent.id}`}
                          className="rounded-full border border-line px-2.5 py-1 font-semibold text-red hover:bg-[#fde8e8] disabled:opacity-40"
                        >
                          Delete
                        </button>
                      </>
                    ) : agent.hidden ? (
                      // A removed built-in: it comes from configuration and cannot be deleted, but the
                      // removal is a durable, reversible hide — offer to restore it.
                      <button
                        type="button"
                        onClick={() => void admin.unhide(agent.id)}
                        disabled={admin.busy}
                        data-testid={`${kind}-restore-${agent.id}`}
                        className="rounded-full border border-line px-2.5 py-1 font-semibold text-ink-2 hover:bg-line-soft disabled:opacity-40"
                      >
                        Restore
                      </button>
                    ) : (
                      // A built-in comes from configuration, so it cannot be edited here (override it
                      // with a custom entry of the same id), but it CAN be removed from the effective
                      // registry — a durable hide the buyer honors on its next request.
                      <button
                        type="button"
                        onClick={() => void admin.hide(agent.id)}
                        disabled={admin.busy}
                        data-testid={`${kind}-remove-${agent.id}`}
                        title="Remove this built-in from the registry the buyer uses. Reversible — Restore brings it back."
                        className="rounded-full border border-line px-2.5 py-1 font-semibold text-red hover:bg-[#fde8e8] disabled:opacity-40"
                      >
                        Remove
                      </button>
                    )}
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {editingId !== null && (
        <RegistrationFormFields
          kind={kind}
          form={form}
          errors={errors}
          busy={admin.busy}
          editingReg={editingReg}
          onChange={setForm}
          onCancel={closeForm}
          onSubmit={() => void submit()}
        />
      )}
    </section>
  );
}

function RegistrationFormFields({
  kind,
  form,
  errors,
  busy,
  editingReg,
  onChange,
  onCancel,
  onSubmit,
}: {
  kind: AgentKind;
  form: RegistrationForm;
  errors: FieldErrors;
  busy: boolean;
  editingReg: AgentRegistration | null;
  onChange: (f: RegistrationForm) => void;
  onCancel: () => void;
  onSubmit: () => void;
}) {
  const set = (patch: Partial<RegistrationForm>) => onChange({ ...form, ...patch });
  const editing = editingReg !== null;

  return (
    <form
      onSubmit={(e) => {
        e.preventDefault();
        onSubmit();
      }}
      className="mt-3 rounded-[12px] border border-line bg-surface p-4"
      data-testid={`${kind}-form`}
    >
      <h3 className="m-0 mb-3 text-[14px] font-bold">
        {editing ? `Edit ${form.id}` : `Add a ${kind} agent`}
      </h3>
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        <Field label="Id" error={errors.id}>
          <input
            value={form.id}
            onChange={(e) => set({ id: e.target.value })}
            // The id is the entry's key; changing it would create a new entry rather than rename one.
            disabled={editing}
            data-testid={`${kind}-field-id`}
            className="w-full rounded-lg border border-line px-2.5 py-1.5 text-[13px] outline-none focus:border-sky disabled:bg-line-soft"
          />
        </Field>
        <Field label="Display name" error={errors.name}>
          <input
            value={form.name}
            onChange={(e) => set({ name: e.target.value })}
            data-testid={`${kind}-field-name`}
            className="w-full rounded-lg border border-line px-2.5 py-1.5 text-[13px] outline-none focus:border-sky"
          />
        </Field>
        <Field label="URL" error={errors.url}>
          <input
            value={form.url}
            onChange={(e) => set({ url: e.target.value })}
            placeholder="https://agent.example/adcp/mcp"
            data-testid={`${kind}-field-url`}
            className="w-full rounded-lg border border-line px-2.5 py-1.5 font-mono text-[13px] outline-none focus:border-sky"
          />
        </Field>
        <Field label="Transport">
          <select
            value={form.transport}
            onChange={(e) => set({ transport: e.target.value === 'a2a' ? 'a2a' : 'mcp' })}
            data-testid={`${kind}-field-transport`}
            className="w-full rounded-lg border border-line px-2.5 py-1.5 text-[13px] outline-none focus:border-sky"
          >
            <option value="mcp">MCP</option>
            <option value="a2a">A2A</option>
          </select>
        </Field>
        {kind === 'seller' && (
          <Field
            label="Agent URL (optional)"
            hint="The seller's canonical AdCP identity, used as check_governance's target_agent. Defaults to the URL above."
          >
            <input
              value={form.agent_url}
              onChange={(e) => set({ agent_url: e.target.value })}
              placeholder="https://agent.example"
              data-testid={`${kind}-field-agent-url`}
              className="w-full rounded-lg border border-line px-2.5 py-1.5 font-mono text-[13px] outline-none focus:border-sky"
            />
          </Field>
        )}
        <Field label="Authentication">
          <select
            value={form.auth_type}
            onChange={(e) => {
              const v = e.target.value;
              set({ auth_type: v === 'cognito_bearer' || v === 'm2m_oauth' ? v : 'static_bearer' });
            }}
            data-testid={`${kind}-field-auth-type`}
            className="w-full rounded-lg border border-line px-2.5 py-1.5 text-[13px] outline-none focus:border-sky"
          >
            <option value="static_bearer">External — bearer token</option>
            <option value="m2m_oauth">External — M2M OAuth (client credentials)</option>
            <option value="cognito_bearer">In-account — this project&apos;s Cognito user</option>
          </select>
        </Field>
        {form.auth_type === 'static_bearer' && (
          <Field
            label="Bearer token"
            error={errors.auth_token}
            hint={
              editing && editingReg?.has_stored_token
                ? 'A token is already stored. Leave blank to keep it, or type a new one to replace it.'
                : 'Stored server-side and used to authenticate to the agent. Never shown again.'
            }
          >
            <input
              type="password"
              value={form.auth_token}
              onChange={(e) => set({ auth_token: e.target.value })}
              placeholder={editing && editingReg?.has_stored_token ? '•••••• (unchanged)' : ''}
              data-testid={`${kind}-field-token`}
              className="w-full rounded-lg border border-line px-2.5 py-1.5 font-mono text-[13px] outline-none focus:border-sky"
            />
          </Field>
        )}
        {form.auth_type === 'm2m_oauth' && (
          <>
            <Field label="Client ID" error={errors.client_id}>
              <input
                value={form.client_id}
                onChange={(e) => set({ client_id: e.target.value })}
                data-testid={`${kind}-field-client-id`}
                className="w-full rounded-lg border border-line px-2.5 py-1.5 text-[13px] outline-none focus:border-sky"
              />
            </Field>
            <Field
              label="Token URL"
              error={errors.token_url}
              hint="The OAuth2 token endpoint (client_credentials grant)."
            >
              <input
                value={form.token_url}
                onChange={(e) => set({ token_url: e.target.value })}
                placeholder="https://…/oauth2/token"
                data-testid={`${kind}-field-token-url`}
                className="w-full rounded-lg border border-line px-2.5 py-1.5 text-[13px] outline-none focus:border-sky"
              />
            </Field>
            <Field
              label="Client secret"
              error={errors.client_secret}
              hint={
                editing && editingReg?.has_stored_secret
                  ? 'A secret is already stored. Leave blank to keep it, or type a new one to replace it.'
                  : 'Stored server-side and used to mint access tokens. Never shown again.'
              }
            >
              <input
                type="password"
                value={form.client_secret}
                onChange={(e) => set({ client_secret: e.target.value })}
                placeholder={editing && editingReg?.has_stored_secret ? '•••••• (unchanged)' : ''}
                data-testid={`${kind}-field-client-secret`}
                className="w-full rounded-lg border border-line px-2.5 py-1.5 font-mono text-[13px] outline-none focus:border-sky"
              />
            </Field>
            <Field label="Scope (optional)" hint="Space-separated OAuth2 scopes, if the provider needs them.">
              <input
                value={form.scope}
                onChange={(e) => set({ scope: e.target.value })}
                data-testid={`${kind}-field-scope`}
                className="w-full rounded-lg border border-line px-2.5 py-1.5 text-[13px] outline-none focus:border-sky"
              />
            </Field>
            <Field label="Audience (optional)" hint="Sent as the audience parameter when the provider requires it.">
              <input
                value={form.audience}
                onChange={(e) => set({ audience: e.target.value })}
                data-testid={`${kind}-field-audience`}
                className="w-full rounded-lg border border-line px-2.5 py-1.5 text-[13px] outline-none focus:border-sky"
              />
            </Field>
          </>
        )}
      </div>
      <div className="mt-4 flex gap-2">
        <button
          type="submit"
          disabled={busy}
          data-testid={`${kind}-form-save`}
          className="rounded-lg bg-ink px-3.5 py-2 text-[13px] font-semibold text-white disabled:opacity-40"
        >
          {busy ? 'Saving…' : editing ? 'Save changes' : `Add ${kind}`}
        </button>
        <button
          type="button"
          onClick={onCancel}
          className="rounded-lg border border-line px-3.5 py-2 text-[13px] font-semibold text-ink-2 hover:bg-line-soft"
        >
          Cancel
        </button>
      </div>
    </form>
  );
}

function Field({
  label,
  error,
  hint,
  children,
}: {
  label: string;
  error?: string | undefined;
  hint?: string | undefined;
  children: React.ReactNode;
}) {
  return (
    <label className="flex flex-col gap-1">
      <span className="text-[13px] font-semibold text-ink-2">{label}</span>
      {children}
      {hint !== undefined && error === undefined && (
        <span className="text-[12px] text-muted">{hint}</span>
      )}
      {error !== undefined && <span className="text-[12px] text-red">{error}</span>}
    </label>
  );
}

function Pill({ children, className }: { children: React.ReactNode; className: string }) {
  return (
    <span className={`rounded-full px-2 py-0.5 text-[12px] font-bold ${className}`}>{children}</span>
  );
}
