import { useEffect, useState } from 'react';
import type { AgentInfo, Api, TeamDefinition, TeamMember, TeamSummary, ModelOption } from '../api';
import { t } from '../i18n';
import { copyText } from '../clipboard';
import { renameTeamMember, removeTeamMember } from '../teams';
import { Dialog } from './Dialog';
import { Popover } from './Popover';
import { IconAgents, IconBack, IconCheck, IconChevron, IconCopy, IconPlus, IconTrash } from './icons';

function Choice({ label, value, options, onPick }: { label: string; value: string; options: { id: string; label: string }[]; onPick: (value: string) => void }) {
  return <Popover label={label} triggerLabel={label} triggerClassName="team-choice" trigger={<><span>{options.find(o => o.id === value)?.label ?? value}</span><IconChevron size={12} open /></>}>
    {close => <>{options.map(o => <button key={o.id} type="button" className="pop-item compact" role="menuitemradio" aria-checked={o.id === value} onClick={() => { onPick(o.id); close(); }}>{o.label}{o.id === value && <IconCheck size={14} />}</button>)}</>}
  </Popover>;
}

/** Model metadata is fetched only while this picker is open. */
function RoleOptions({ api, agent, member, field, onPick }: { api: Api; agent: AgentInfo | undefined; member: TeamMember; field: 'model' | 'effort'; onPick: (value: string | undefined) => void }) {
  const [models, setModels] = useState<ModelOption[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const load = () => { setError(null); void api.models(member.agent).then(setModels).catch(e => setError(String(e))); };
  useEffect(load, [api, member.agent]);
  if (error) return <><p className="teams-error">{error}</p><button type="button" className="pop-item" onClick={load}>{t('teams.retry')}</button></>;
  if (!models) return <p className="muted">{t('common.loading')}</p>;
  const options = field === 'model' ? models : (models.find(m => m.id === member.model)?.efforts ?? agent?.capabilities.effortLevels ?? []).map(id => ({ id, label: id }));
  return <><button type="button" className="pop-item compact" onClick={() => onPick(undefined)}>{t('teams.default')}</button>{options.map(o => <button type="button" className="pop-item compact" key={o.id} onClick={() => onPick(o.id)}>{o.label}{member[field] === o.id && <IconCheck size={14} />}</button>)}</>;
}
function RoleSetting({ api, agent, member, field, onPick }: { api: Api; agent: AgentInfo | undefined; member: TeamMember; field: 'model' | 'effort'; onPick: (value: string | undefined) => void }) {
  return <div className="team-field"><span>{t(`teams.${field}`)}</span><Popover label={t(`teams.${field}`)} triggerLabel={t(`teams.${field}`)} triggerClassName="team-choice" trigger={<><span>{member[field] || t('teams.default')}</span><IconChevron size={12} open /></>}>
    {close => <RoleOptions api={api} agent={agent} member={member} field={field} onPick={value => { onPick(value); close(); }} />}
  </Popover></div>;
}

export function TeamsPage({ api, agents, onReload }: { api: Api; agents: AgentInfo[]; onReload: () => void }) {
  const [teams, setTeams] = useState<TeamSummary[] | null>(null);
  const [draft, setDraft] = useState<TeamDefinition | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [removing, setRemoving] = useState<TeamSummary | null>(null);
  const [importing, setImporting] = useState(false);
  const [json, setJson] = useState('');
  const [copied, setCopied] = useState<string | null>(null);
  const fail = (e: unknown) => setError(e instanceof Error ? e.message : String(e));
  const reload = async () => { setError(null); try { setTeams(await api.teams()); onReload(); } catch (e) { fail(e); } };
  useEffect(() => { void reload(); }, [api]);
  const run = async (action: () => Promise<void>) => { setBusy(true); setError(null); try { await action(); } catch (e) { fail(e); } finally { setBusy(false); } };
  const create = () => setDraft({ name: '', entry: 'lead', members: [{ name: 'lead', agent: agents.find(a => a.available && !a.hidden && a.capabilities.coordination)?.id ?? '' }], relations: [], budget: { maxDeliveries: 30 } });
  const updateMember = (index: number, patch: Partial<TeamMember>) => setDraft(d => d && ({ ...d, members: d.members.map((m, i) => i === index ? { ...m, ...patch } : m) }));
  // Hidden instances cannot be picked; one a draft already uses stays selectable so editing shows it.
  const eligible = agents.filter(a => a.capabilities.coordination && (!a.hidden || draft?.members.some(m => m.agent === a.id)));

  return <section className="teams-page">
    <div className="teams-page-head">
      <div><h1>{draft ? draft.id ? draft.name : t('teams.new') : t('teams.title')}</h1><p>{t('teams.intro')}</p></div>
      {draft ? <button className="ghost" disabled={busy} onClick={() => { setDraft(null); setError(null); }}><IconBack size={16} />{t('common.cancel')}</button> : <IconAgents size={28} />}
    </div>
    {error && <div className="teams-error" role="alert">{error}{!draft && !importing && <button className="ghost" onClick={() => void reload()}>{t('teams.retry')}</button>}</div>}
    {!draft ? <>
      <div className="teams-actions"><button className="primary" onClick={create}><IconPlus size={16} />{t('teams.new')}</button><button className="ghost" disabled={busy} onClick={() => void run(async () => setDraft(await api.teamTemplate()))}>{t('teams.template')}</button><button className="ghost" onClick={() => { setJson(''); setImporting(true); }}>{t('teams.import')}</button></div>
      {teams === null ? !error && <p className="muted">{t('common.loading')}</p> : !teams.length ? <div className="teams-empty"><IconAgents size={32} /><p>{t('teams.empty')}</p></div> : <div className="teams-list">{teams.map(team => <article key={team.id} className="team-definition">
        <div className="team-definition-main"><h2>{team.name}</h2><p>{team.description}</p><span className="muted">{t('teams.members', team.memberCount)} · {t('teams.entry')}: {team.entry}</span></div>
        <div className="teams-actions"><button className="ghost" disabled={busy} onClick={() => void run(async () => setDraft(await api.team(team.id)))}>{t('teams.edit')}</button><button className="icon-btn" aria-label={t('teams.export')} title={copied === team.id ? t('teams.copied') : t('teams.export')} onClick={() => void run(async () => { await copyText(JSON.stringify(await api.exportTeam(team.id), null, 2)); setCopied(team.id); })}>{copied === team.id ? <IconCheck size={16} /> : <IconCopy size={16} />}</button><button className="icon-btn" aria-label={t('teams.delete')} onClick={() => setRemoving(team)}><IconTrash size={16} /></button></div>
      </article>)}</div>}
    </> : <form onSubmit={e => { e.preventDefault(); void run(async () => { await api.saveTeam(draft); setDraft(null); await reload(); }); }}>
      <div className="team-fields"><label>{t('teams.name')}<input required value={draft.name} onChange={e => setDraft({ ...draft, name: e.target.value })} /></label><label>{t('teams.description')}<input value={draft.description ?? ''} onChange={e => setDraft({ ...draft, description: e.target.value })} /></label></div>
      <div className="team-members">{draft.members.map((member, index) => <article className="team-member-editor" key={index}>
        <div className="team-member-head"><strong>{member.name || t('teams.memberName')}</strong><button type="button" className="icon-btn" disabled={draft.members.length === 1} aria-label={t('teams.removeMember')} onClick={() => setDraft(removeTeamMember(draft, index))}><IconTrash size={16} /></button></div>
        <div className="team-fields"><label>{t('teams.memberName')}<input required maxLength={40} value={member.name} onChange={e => setDraft(renameTeamMember(draft, index, e.target.value))} /></label><div className="team-field"><span>{t('teams.instance')}</span><Choice label={t('teams.instance')} value={member.agent} options={eligible.map(a => ({ id: a.id, label: a.label }))} onPick={agent => updateMember(index, { agent, model: undefined, effort: undefined })} />{!eligible.some(a => a.id === member.agent && a.available) && <span className="teams-error">{t('teams.unavailable')}</span>}</div>
          <RoleSetting api={api} agent={agents.find(a => a.id === member.agent)} member={member} field="model" onPick={model => updateMember(index, { model, effort: undefined })} /><RoleSetting api={api} agent={agents.find(a => a.id === member.agent)} member={member} field="effort" onPick={effort => updateMember(index, { effort })} />
          <label className="team-field-wide">{t('teams.cwd')}<input value={member.cwd ?? ''} onChange={e => updateMember(index, { cwd: e.target.value || undefined })} /></label><label className="team-field-wide">{t('teams.instruction')}<textarea rows={3} maxLength={4000} value={member.instruction ?? ''} onChange={e => updateMember(index, { instruction: e.target.value })} /></label>
        </div>
        <div className="team-links"><span>{t('teams.delegates')}</span>{draft.members.filter((_, i) => i !== index).map((other, i) => {
          const selected = draft.relations.some(r => r.from.includes(member.name) && r.to === other.name);
          return <button key={i} type="button" className={`team-link${selected ? ' selected' : ''}`} aria-pressed={selected} onClick={() => setDraft({ ...draft, relations: selected ? draft.relations.map(r => r.to === other.name ? { ...r, from: r.from.filter(n => n !== member.name) } : r).filter(r => r.from.length) : [...draft.relations, { kind: 'delegate', from: [member.name], to: other.name }] })}>{selected && <IconCheck size={13} />}{other.name}</button>;
        })}</div>
      </article>)}</div>
      <button type="button" className="ghost" disabled={draft.members.length >= 12} onClick={() => { let n = draft.members.length + 1; while (draft.members.some(m => m.name === `member-${n}`)) n++; setDraft({ ...draft, members: [...draft.members, { name: `member-${n}`, agent: eligible.find(a => a.available)?.id ?? '' }] }); }}><IconPlus size={16} />{t('teams.addMember')}</button>
      <div className="team-fields team-task-settings"><div className="team-field"><span>{t('teams.entry')}</span><Choice label={t('teams.entry')} value={draft.entry} options={draft.members.map(m => ({ id: m.name, label: m.name }))} onPick={entry => setDraft({ ...draft, entry })} /></div><label>{t('teams.budget')}<input inputMode="numeric" value={draft.budget?.maxDeliveries ?? 30} onChange={e => setDraft({ ...draft, budget: { maxDeliveries: Number(e.target.value) } })} /></label></div>
      <div className="team-relations"><h2>{t('teams.relations')}</h2>{draft.relations.length ? draft.relations.map((r, i) => <p key={i}>{r.from.join(', ')} → {r.to}</p>) : <p className="muted">{t('teams.noRelations')}</p>}</div>
      <div className="teams-save"><button className="primary" disabled={busy}>{t('teams.save')}</button></div>
    </form>}
    {removing && <Dialog title={t('teams.delete')} message={t('teams.deleteHint')} confirmLabel={t('teams.delete')} danger confirmDisabled={busy} onCancel={() => setRemoving(null)} onConfirm={() => void run(async () => { await api.deleteTeam(removing.id); setRemoving(null); await reload(); })} />}
    {importing && <Dialog title={t('teams.import')} confirmLabel={t('teams.import')} confirmDisabled={busy || !json.trim()} onCancel={() => setImporting(false)} onConfirm={() => void run(async () => { await api.importTeam(JSON.parse(json)); setImporting(false); await reload(); })}><label className="dialog-field">{t('teams.json')}<textarea rows={12} value={json} onChange={e => setJson(e.target.value)} /></label>{error && <p className="teams-error" role="alert">{error}</p>}</Dialog>}
  </section>;
}
