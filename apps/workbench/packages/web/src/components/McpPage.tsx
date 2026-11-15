import { useCallback, useEffect, useMemo, useRef, useState, type CSSProperties, type MouseEvent } from 'react';
import { Api, type AgentInfo, type HostInfo, type McpEntry, type McpInstallOutcome, type McpListing, type McpServer } from '../api';
import { copyText } from '../clipboard';
import { useIsNarrow } from '../hooks';
import { Dialog, DialogSurface } from './Dialog';
import { Menu, type MenuEntry } from './Menu';
import { AgentMark, IconCheck, IconClose, IconCopy, IconEdit, IconInfo, IconMore, IconPlus, IconRefresh, IconRestore, IconSearch, IconTrash } from './icons';
import { t } from '../i18n';

/**
 * The MCP servers of every agent on every machine, in one view: a row per server, a column per
 * machine, and in each cell the marks of the agents configured with it. The part no config file
 * knows — which business line a server belongs to and what it is for — lives in the hub's own
 * registry and is edited here; servers travel between machines through each machine's daemon,
 * which writes its own agents' configs. Nothing is written unless asked, and only MCP entries.
 */

interface Props {
  /** The client of the machine on screen. */
  api: Api;
  /** To open one client per other machine, through the hub's proxy. */
  token: string;
  /** The machine on screen: one of the hub's others, or null for the hub's own. */
  hostId: string | null;
  hosts: HostInfo[];
  /** The agents of the machine on screen, for their marks. */
  agents: AgentInfo[];
  onError: (e: unknown) => void;
}

/** One machine's column: what it holds, and the marks to draw it with. */
interface MachineColumn {
  id: string | null;
  label: string;
  agents: AgentInfo[];
  listing: McpListing | null;
  error: string | null;
}

/** One agent on one machine having the server, with the config as it stands there. */
interface McpPlace {
  machine: MachineColumn;
  agentId: string;
  server: McpServer;
}

/** A server row: where it is machine by machine, and the registry's note about it. */
interface McpRow {
  name: string;
  entry: McpEntry | null;
  places: McpPlace[];
  /** What installs hand over: the registered config, else the first copy found. */
  config: Record<string, unknown> | null;
}

const PLACE_KEY = '\u0000';

/** The one-line shape of a server: the command with its args, or the address. */
function configSummary(config: Record<string, unknown>): string {
  if (typeof config.command === 'string' && config.command) {
    const args = Array.isArray(config.args) ? config.args.filter((a) => typeof a === 'string').join(' ') : '';
    return args ? `${config.command} ${args}` : config.command;
  }
  if (typeof config.url === 'string' && config.url) return config.url;
  return t('mcp.unknownConfig');
}

/** Split an args line the way a shell would, minus the escapes nobody types here. */
function splitArgs(text: string): string[] {
  const out: string[] = [];
  for (const match of text.matchAll(/"([^"]*)"|'([^']*)'|(\S+)/g)) out.push(match[1] ?? match[2] ?? match[3]);
  return out;
}

/** The mark of an agent, with its name for hover and screen readers. */
function Mark({ agent, size = 16 }: { agent: AgentInfo; size?: number }) {
  return (
    <span className="skill-mark" title={agent.label} aria-label={agent.label}>
      {agent.icon ? <AgentMark path={agent.icon} size={size} /> : <span className="skill-mark-name">{agent.label}</span>}
    </span>
  );
}

function marksFor(column: MachineColumn, agentIds: string[]): JSX.Element[] {
  return agentIds
    .map((id) => column.agents.find((a) => a.id === id))
    .filter((a): a is AgentInfo => Boolean(a))
    .map((agent) => <Mark key={agent.id} agent={agent} />);
}

/** Config as labeled lines for the detail dialog. Env values are part of the config, so they show
 * here — the list stays quiet about them. */
function ConfigLines({ config }: { config: Record<string, unknown> | null }) {
  if (!config || Object.keys(config).length === 0) return <p className="panel-empty">{t('mcp.noConfig')}</p>;
  const lines: Array<[string, string]> = [];
  for (const [key, value] of Object.entries(config)) {
    if (key === 'env' && value && typeof value === 'object' && !Array.isArray(value)) {
      for (const [name, envValue] of Object.entries(value as Record<string, unknown>)) lines.push([`${key}.${name}`, String(envValue)]);
    } else if (Array.isArray(value)) {
      lines.push([key, value.map((v) => String(v)).join(' ')]);
    } else if (value !== null && typeof value === 'object') {
      lines.push([key, JSON.stringify(value)]);
    } else {
      lines.push([key, String(value)]);
    }
  }
  return (
    <dl className="mcp-config">
      {lines.map(([key, value]) => (
        <div key={key} className="mcp-config-row">
          <dt>{key}</dt>
          <dd title={value}>{value}</dd>
        </div>
      ))}
    </dl>
  );
}

export function McpPage({ api, token, hostId, hosts, agents, onError }: Props) {
  const narrow = useIsNarrow();
  const loadGeneration = useRef(0);
  const [columns, setColumns] = useState<MachineColumn[]>([]);
  const [entries, setEntries] = useState<McpEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState('');
  /** Which business line the list is narrowed to; null shows all. '' is the ungrouped one. */
  const [line, setLine] = useState<string | null>(null);
  const [menu, setMenu] = useState<{ row: McpRow; x: number; y: number } | null>(null);
  const [detail, setDetail] = useState<McpRow | null>(null);
  /** The note dialog: `row` null means a fresh registration nothing found yet. */
  const [note, setNote] = useState<{ row: McpRow | null } | null>(null);
  const [install, setInstall] = useState<{ row: McpRow } | null>(null);
  const [removing, setRemoving] = useState<{ row: McpRow; place: McpPlace } | null>(null);
  const [deleting, setDeleting] = useState<{ row: McpRow } | null>(null);

  /** The client of any machine: the one on screen, or one opened through the hub's proxy. */
  const clientFor = useCallback(
    (id: string | null): Api => (id === (hostId ?? null) ? api : new Api(token, '', id)),
    [api, token, hostId],
  );

  const load = useCallback(async () => {
    const generation = ++loadGeneration.current;
    const wanted: Array<{ id: string | null; label: string }> = [
      { id: hostId ?? null, label: hostId ? (hosts.find((h) => h.id === hostId)?.label ?? hostId) : t('hosts.local') },
      ...(hostId ? [{ id: null, label: t('hosts.local') }] : []),
      ...hosts.filter((h) => h.id !== hostId).map((h) => ({ id: h.id, label: h.label })),
    ];
    setColumns((prev) =>
      wanted.map((w) => {
        const old = prev.find((c) => c.id === w.id);
        return { id: w.id, label: w.label, agents: old?.agents ?? (w.id === (hostId ?? null) ? agents : []), listing: old?.listing ?? null, error: old?.error ?? null };
      }),
    );
    setLoading(true);
    await Promise.all([
      ...wanted.map(async (w) => {
        const client = clientFor(w.id);
        try {
          const listing = await client.mcp();
          let agentList: AgentInfo[] = w.id === (hostId ?? null) ? agents : [];
          if (w.id !== (hostId ?? null)) {
            try {
              agentList = await client.agents();
            } catch {
              // The marks are nice to have; the listing says what the column shows either way.
            }
          }
          if (generation !== loadGeneration.current) return;
          setColumns((prev) => prev.map((c) => (c.id === w.id ? { ...c, agents: agentList, listing, error: null } : c)));
        } catch (e) {
          if (generation !== loadGeneration.current) return;
          setColumns((prev) => prev.map((c) => (c.id === w.id ? { ...c, listing: null, error: e instanceof Error ? e.message : String(e) } : c)));
        }
      }),
      clientFor(null)
        .mcpEntries()
        .then((list) => {
          if (generation !== loadGeneration.current) return;
          setEntries(list);
        })
        .catch((e) => {
          if (generation !== loadGeneration.current) return;
          setEntries([]);
          onError(e);
        }),
    ]);
    if (generation === loadGeneration.current) setLoading(false);
  }, [clientFor, hostId, hosts, agents, onError]);

  useEffect(() => {
    void load();
  }, [load]);

  /** Everything found, narrowed only by search. The line filter is applied on top, so the chips
   * never lose a line just because it is the one being looked at. */
  const allRows = useMemo<McpRow[]>(() => {
    const names = new Set<string>();
    for (const column of columns) for (const agent of column.listing?.agents ?? []) for (const server of agent.servers) names.add(server.name);
    for (const entry of entries) names.add(entry.name);
    const want = search.trim().toLowerCase();
    const out: McpRow[] = [];
    for (const name of [...names].sort((a, b) => a.localeCompare(b))) {
      const places: McpPlace[] = [];
      for (const column of columns) {
        for (const agentListing of column.listing?.agents ?? []) {
          const server = agentListing.servers.find((s) => s.name === name);
          if (server) places.push({ machine: column, agentId: agentListing.agent, server });
        }
      }
      const entry = entries.find((e) => e.name === name) ?? null;
      if (!places.length && !entry) continue;
      if (want) {
        const haystack = [name, entry?.businessLine ?? '', entry?.description ?? '', ...places.map((p) => configSummary(p.server.config))].join(' ').toLowerCase();
        if (!haystack.includes(want)) continue;
      }
      out.push({ name, entry, places, config: entry?.server ?? places[0]?.server.config ?? null });
    }
    return out;
  }, [columns, entries, search]);

  /** The business lines worth a filter chip: the ones in the registry, plus "ungrouped" when something has none. */
  const lines = useMemo(() => {
    const seen = new Set(entries.map((e) => e.businessLine).filter(Boolean));
    if (allRows.some((row) => !(row.entry?.businessLine ?? ''))) seen.add('');
    return [...seen].sort((a, b) => (a === '' ? 1 : b === '' ? -1 : a.localeCompare(b)));
  }, [entries, allRows]);

  const rows = useMemo(() => (line === null ? allRows : allRows.filter((row) => (row.entry?.businessLine ?? '') === line)), [allRows, line]);

  /** Rows grouped by business line, the ungrouped ones last. */
  const groups = useMemo(() => {
    const map = new Map<string, McpRow[]>();
    for (const row of rows) {
      const key = row.entry?.businessLine ?? '';
      const list = map.get(key) ?? [];
      list.push(row);
      map.set(key, list);
    }
    return [...map.entries()].sort(([a], [b]) => (a === '' ? 1 : b === '' ? -1 : a.localeCompare(b)));
  }, [rows]);

  const gridStyle = { '--cols': String(Math.max(columns.length, 1)), ...(columns.length <= 1 ? { gridTemplateColumns: 'minmax(0, 1fr) 34px' } : {}) } as CSSProperties;
  const single = columns.length <= 1;
  /** Places whose config differs from the one this row would hand out: worth pointing at. */
  const differs = (row: McpRow): boolean =>
    row.config !== null && row.places.some((p) => JSON.stringify(p.server.config) !== JSON.stringify(row.config));

  const openMenu = (row: McpRow) => (e: MouseEvent<HTMLButtonElement>) => {
    const rect = e.currentTarget.getBoundingClientRect();
    setMenu({ row, x: rect.right - 220, y: rect.bottom + 4 });
  };

  const menuItems = (row: McpRow): MenuEntry[] => [
    { id: 'view', label: t('mcp.details'), icon: IconInfo, onSelect: () => setDetail(row) },
    { id: 'note', label: row.entry ? t('mcp.editNote') : t('mcp.addNote'), icon: IconEdit, onSelect: () => setNote({ row }) },
    { id: 'install', label: t('mcp.installTo'), icon: IconRestore, onSelect: () => setInstall({ row }) },
    'separator',
    ...row.places.map((place) => ({
      id: `remove:${place.machine.id ?? ''}:${place.agentId}`,
      label: t('mcp.removeFrom', `${place.machine.label} · ${place.machine.agents.find((a) => a.id === place.agentId)?.label ?? place.agentId}`),
      icon: IconTrash,
      danger: true,
      onSelect: () => setRemoving({ row, place }),
    })),
    ...(row.entry
      ? [
          'separator' as MenuEntry,
          {
            id: 'unregister',
            label: t('mcp.unregister'),
            icon: IconTrash,
            danger: true,
            onSelect: () => setDeleting({ row }),
          } as MenuEntry,
        ]
      : []),
    'separator',
    {
      id: 'copy',
      label: t('mcp.copyConfig'),
      icon: IconCopy,
      disabled: row.config === null,
      onSelect: () => {
        if (row.config) void copyText(JSON.stringify({ mcpServers: { [row.name]: row.config } }, null, 2));
      },
    },
  ];

  return (
    <div className="skills-page mcp-page">
      <header className="skills-head">
        <h1 className="skills-title">MCP</h1>
        <p className="skills-hint">{t('mcp.hint')}</p>
        <div className="skills-tools">
          <label className="skills-search">
            <IconSearch size={15} />
            <input value={search} onChange={(e) => setSearch(e.target.value)} placeholder={t('mcp.searchPlaceholder')} aria-label={t('mcp.search')} />
            {search && (
              <button className="icon-btn" aria-label={t('skills.clearSearch')} onClick={() => setSearch('')}>
                <IconClose size={14} />
              </button>
            )}
          </label>
          <button className="icon-btn" aria-label={t('skills.reload')} title={t('skills.reload')} onClick={() => void load()}>
            <IconRefresh size={17} />
          </button>
          <button className="primary" onClick={() => setNote({ row: null })}>
            <IconPlus size={15} />
            {t('mcp.register')}
          </button>
        </div>
        {lines.length > 1 && (
          <div className="mcp-lines" role="group" aria-label={t('mcp.filterLine')}>
            <button type="button" className={`mcp-line${line === null ? ' on' : ''}`} onClick={() => setLine(null)}>
              {t('trace.all')}
            </button>
            {lines.map((l) => (
              <button key={l} type="button" className={`mcp-line${line === l ? ' on' : ''}`} onClick={() => setLine(line === l ? null : l)}>
                {l === '' ? t('mcp.ungrouped') : l}
              </button>
            ))}
          </div>
        )}
      </header>

      {columns.length > 1 && (
        <div className="skills-grid skills-grid-head" style={gridStyle} role="row">
          <div className="skills-cell skills-cell-name" />
          {columns.map((c) => (
            <div key={c.id ?? 'local'} className="skills-cell skills-cell-machine" role="columnheader">
              {c.label}
              {c.listing === null && c.error && <span className="skills-machine-error" title={c.error}>{t('skills.unreachable')}</span>}
            </div>
          ))}
        </div>
      )}

      <div className="skills-list">
        {columns.filter((c) => c.error).map((c) => (
          <p className="skill-sync-error" key={c.id ?? 'local'}>
            {t('skills.readFailed', c.label)}
          </p>
        ))}
        {rows.length === 0 && (
          <div className="panel-empty">{loading ? t('mcp.loading') : search || line !== null ? t('mcp.noMatch') : t('mcp.empty')}</div>
        )}
        {groups.map(([group, groupRows]) => (
          <section key={group} className="mcp-group">
            <div className="mcp-group-head">
              <span className="mcp-group-name">{group === '' ? t('mcp.ungrouped') : group}</span>
              <span className="mcp-group-count">{groupRows.length}</span>
            </div>
            {groupRows.map((row) => (
              <div key={row.name} className="skills-grid skills-row" style={gridStyle} role="row">
                <button type="button" className="skills-cell skills-cell-name" role="cell" onClick={() => setDetail(row)} title={t('mcp.details')}>
                  <span className="skills-name-col">
                    <span className="skills-name">
                      {row.name}
                      {row.entry?.server && <span className="mcp-badge" title={t('mcp.registeredHint')}>{t('mcp.registered')}</span>}
                      {differs(row) && <span className="skills-differs" title={t('mcp.differsHint')}>{t('mcp.differs')}</span>}
                    </span>
                    {(row.entry?.description || configSummary(row.config ?? {})) && (
                      <span className="skills-desc">{row.entry?.description || configSummary(row.config ?? {})}</span>
                    )}
                    <span className="skills-meta">
                      {row.places.length
                        ? t('mcp.installedOn', new Set(row.places.map((p) => p.machine.id)).size, row.places.length)
                        : t('mcp.notInstalled')}
                      {row.entry?.businessLine ? ` · ${row.entry.businessLine}` : ''}
                    </span>
                  </span>
                  {single && row.places.length > 0 && (
                    <span className="skill-marks" title={row.places.map((p) => p.machine.agents.find((a) => a.id === p.agentId)?.label ?? p.agentId).join('、')}>
                      {marksFor(row.places[0].machine, row.places.filter((p) => p.machine === row.places[0].machine).map((p) => p.agentId))}
                    </span>
                  )}
                </button>
                {!single &&
                  columns.map((column) => {
                    const here = row.places.filter((p) => p.machine === column);
                    return (
                      <div key={column.id ?? 'local'} className="skills-cell skills-cell-machine" role="cell">
                        {here.length > 0 ? (
                          <span className="skill-marks" title={t('skills.installedOn', column.label)}>{marksFor(column, here.map((p) => p.agentId))}</span>
                        ) : (
                          <button type="button" className="skill-add" disabled={!column.listing || row.config === null} title={t('mcp.installOn', column.label)} onClick={() => setInstall({ row })}>
                            <IconPlus size={11} /> {t('mcp.install')}
                          </button>
                        )}
                      </div>
                    );
                  })}
                <button type="button" className="row-more" aria-label={t('skills.actions', row.name)} onClick={openMenu(row)}>
                  <IconMore size={15} />
                </button>
                {narrow && !single && (
                  <div className="skills-mobile-machines">
                    {columns.map((column) => {
                      const here = row.places.filter((p) => p.machine === column);
                      return (
                        <span key={column.id ?? 'local'} className="skills-mobile-machine">
                          <span className="skills-mobile-label">{column.label}</span>
                          {here.length ? marksFor(column, here.map((p) => p.agentId)) : (
                            <button type="button" className="skill-add" disabled={!column.listing || row.config === null} onClick={() => setInstall({ row })}>
                              <IconPlus size={11} /> {t('mcp.install')}
                            </button>
                          )}
                        </span>
                      );
                    })}
                  </div>
                )}
              </div>
            ))}
          </section>
        ))}
      </div>

      {menu && <Menu label={t('skills.actions', menu.row.name)} x={menu.x} y={menu.y} items={menuItems(menu.row)} onClose={() => setMenu(null)} />}
      {detail && <McpDetail row={detail} onClose={() => setDetail(null)} onNote={() => { setDetail(null); setNote({ row: detail }); }} />}
      {note && <McpNoteDialog row={note.row} client={clientFor(null)} onClose={() => setNote(null)} onDone={load} onError={onError} />}
      {install && <McpInstall row={install.row} columns={columns} clientFor={clientFor} onClose={() => setInstall(null)} onDone={load} onError={onError} />}
      {removing && (
        <Dialog
          title={t('hosts.removeTitle', removing.row.name)}
          message={t('mcp.removeMessage', removing.place.machine.label, removing.place.machine.agents.find((a) => a.id === removing.place.agentId)?.label ?? removing.place.agentId, removing.place.server.sourcePath)}
          confirmLabel={t('hosts.remove')}
          danger
          onConfirm={() => {
            const target = removing;
            setRemoving(null);
            void clientFor(target.place.machine.id)
              .mcpRemove(target.place.agentId, target.row.name)
              .then(load)
              .catch(onError);
          }}
          onCancel={() => setRemoving(null)}
        />
      )}
      {deleting && (
        <Dialog
          title={t('mcp.unregisterTitle', deleting.row.name)}
          message={t('mcp.unregisterMessage', Boolean(deleting.row.entry?.server))}
          confirmLabel={t('mcp.unregister')}
          danger
          onConfirm={() => {
            const target = deleting;
            setDeleting(null);
            void clientFor(null)
              .mcpDeleteEntry(target.row.name)
              .then(load)
              .catch(onError);
          }}
          onCancel={() => setDeleting(null)}
        />
      )}
    </div>
  );
}

/** One server in full: the words about it, the config, and every agent that has it. */
function McpDetail({ row, onClose, onNote }: { row: McpRow; onClose: () => void; onNote: () => void }) {
  return (
    <DialogSurface className="skill-dialog" labelledBy="mcp-detail-title" onClose={onClose}>
      <div className="skill-dialog-head">
        <h2 id="mcp-detail-title" className="skill-dialog-title">
          {row.name}
        </h2>
        <button className="icon-btn" aria-label={t('common.close')} onClick={onClose}>
          <IconClose size={16} />
        </button>
      </div>
      <div className="skill-dialog-body">
        <p className="mcp-detail-line">
          {row.entry?.businessLine ? <span className="mcp-line on static">{row.entry.businessLine}</span> : <span className="mcp-detail-muted">{t('mcp.noLine')}</span>}
          {row.entry?.description ? <span className="mcp-detail-desc">{row.entry.description}</span> : <button className="mcp-detail-note" onClick={onNote}>{t('mcp.describe')}</button>}
        </p>
        <ConfigLines config={row.config} />
        <div className="mcp-places">
          <div className="skill-sync-label">{t('mcp.where')}</div>
          {row.places.length === 0 && <p className="panel-empty">{t('mcp.notInstalledDot')}</p>}
          {row.places.map((place) => (
            <p key={`${place.machine.id ?? ''}:${place.agentId}`} className="mcp-place">
              <span className="skill-marks">{marksFor(place.machine, [place.agentId])}</span>
              <span>
                {place.machine.label} · {place.machine.agents.find((a) => a.id === place.agentId)?.label ?? place.agentId}
              </span>
              <span className="mcp-place-path" title={place.server.sourcePath}>
                {place.server.sourcePath}
              </span>
            </p>
          ))}
        </div>
      </div>
    </DialogSurface>
  );
}

/** The registry's note about one name: business line, purpose, and optionally a config to hand out. */
function McpNoteDialog({ row, client, onClose, onDone, onError }: { row: McpRow | null; client: Api; onClose: () => void; onDone: () => void; onError: (e: unknown) => void }) {
  const entry = row?.entry ?? null;
  const [name, setName] = useState(row?.name ?? '');
  const [businessLine, setBusinessLine] = useState(entry?.businessLine ?? '');
  const [description, setDescription] = useState(entry?.description ?? '');
  const server = entry?.server ?? null;
  const [kind, setKind] = useState<'command' | 'url'>(server && typeof server.url === 'string' ? 'url' : 'command');
  const [command, setCommand] = useState(typeof server?.command === 'string' ? server.command : '');
  const [args, setArgs] = useState(Array.isArray(server?.args) ? server.args.map(String).join(' ') : '');
  const [env, setEnv] = useState(
    server?.env && typeof server.env === 'object' ? Object.entries(server.env as Record<string, unknown>).map(([k, v]) => `${k}=${v}`).join('\n') : '',
  );
  const [url, setUrl] = useState(typeof server?.url === 'string' ? server.url : '');
  const [busy, setBusy] = useState(false);

  const save = async () => {
    setBusy(true);
    try {
      let config: Record<string, unknown> | null = null;
      if (kind === 'command' && command.trim()) {
        config = { command: command.trim(), ...(args.trim() ? { args: splitArgs(args) } : {}) };
        const envPairs = env
          .split('\n')
          .map((l) => l.trim())
          .filter((l) => l && !l.startsWith('#'));
        if (envPairs.length) {
          const envObject: Record<string, string> = {};
          for (const pair of envPairs) {
            const at = pair.indexOf('=');
            if (at > 0) envObject[pair.slice(0, at)] = pair.slice(at + 1);
          }
          if (Object.keys(envObject).length) config.env = envObject;
        }
      } else if (kind === 'url' && url.trim()) {
        config = { url: url.trim() };
      }
      await client.mcpSaveEntry({ name: name.trim(), businessLine: businessLine.trim(), description: description.trim(), server: config });
      onClose();
      onDone();
    } catch (e) {
      onError(e);
    } finally {
      setBusy(false);
    }
  };

  return (
    <DialogSurface className="skill-dialog" labelledBy="mcp-note-title" onClose={onClose} busy={busy}>
      <div className="skill-dialog-head">
        <h2 id="mcp-note-title" className="skill-dialog-title">
          {row ? t('mcp.noteTitle', row.name) : t('mcp.register')}
        </h2>
        <button className="icon-btn" aria-label={t('common.close')} onClick={onClose} disabled={busy}>
          <IconClose size={16} />
        </button>
      </div>
      <div className="skill-dialog-body mcp-note">
        <label className="mcp-field">
          <span>{t('mcp.name')}</span>
          <input value={name} onChange={(e) => setName(e.target.value)} disabled={Boolean(row)} placeholder={t('mcp.namePlaceholder')} autoFocus={!row} />
        </label>
        <label className="mcp-field">
          <span>{t('mcp.line')}</span>
          <input value={businessLine} onChange={(e) => setBusinessLine(e.target.value)} placeholder={t('mcp.linePlaceholder')} />
        </label>
        <label className="mcp-field">
          <span>{t('mcp.purpose')}</span>
          <textarea value={description} onChange={(e) => setDescription(e.target.value)} rows={3} placeholder={t('mcp.purposePlaceholder')} />
        </label>
        <div className="mcp-field">
          <span>{t('mcp.configLabel')}</span>
          <div className="mcp-kind">
            <label className={`skill-source${kind === 'command' ? ' picked' : ''}`}>
              <input type="radio" name="mcp-kind" checked={kind === 'command'} onChange={() => setKind('command')} disabled={busy} />
              <span>{t('mcp.localCommand')}</span>
            </label>
            <label className={`skill-source${kind === 'url' ? ' picked' : ''}`}>
              <input type="radio" name="mcp-kind" checked={kind === 'url'} onChange={() => setKind('url')} disabled={busy} />
              <span>{t('mcp.httpUrl')}</span>
            </label>
          </div>
          {kind === 'command' ? (
            <>
              <input value={command} onChange={(e) => setCommand(e.target.value)} placeholder={t('mcp.commandPlaceholder')} />
              <input value={args} onChange={(e) => setArgs(e.target.value)} placeholder={t('mcp.argsPlaceholder')} />
              <textarea value={env} onChange={(e) => setEnv(e.target.value)} rows={2} placeholder={t('mcp.envPlaceholder')} />
            </>
          ) : (
            <input value={url} onChange={(e) => setUrl(e.target.value)} placeholder="https://example.com/mcp" />
          )}
          <p className="mcp-field-hint">{t('mcp.configHint')}</p>
        </div>
      </div>
      <div className="dialog-actions">
        <button className="ghost" onClick={onClose} disabled={busy}>
          {t('common.cancel')}
        </button>
        <button className="primary" disabled={busy || !name.trim()} onClick={() => void save()}>
          {busy ? t('mcp.saving') : t('actions.save')}
        </button>
      </div>
    </DialogSurface>
  );
}

type InstallPhase = { running: true } | { done: Map<string, McpInstallOutcome & { machineId: string | null }> };

const isRunning = (phase: InstallPhase | null): boolean => phase !== null && 'running' in phase;
const doneOf = (phase: InstallPhase | null): Map<string, McpInstallOutcome & { machineId: string | null }> | null =>
  phase !== null && !('running' in phase) ? phase.done : null;

/** One server onto any machine's agents: a config picked among its copies, written by each target
 * machine's own daemon. `exists` asks before it overwrites a config the target changed itself. */
function McpInstall({
  row,
  columns,
  clientFor,
  onClose,
  onDone,
  onError,
}: {
  row: McpRow;
  columns: MachineColumn[];
  clientFor: (id: string | null) => Api;
  onClose: () => void;
  onDone: () => void;
  onError: (e: unknown) => void;
}) {
  /** The configs this server has: the registered one plus one per distinct copy found. */
  const sources = useMemo(() => {
    const out: Array<{ key: string; label: string; note: string; config: Record<string, unknown> }> = [];
    if (row.entry?.server) out.push({ key: 'entry', label: t('mcp.registeredConfig'), note: configSummary(row.entry.server), config: row.entry.server });
    const seen = new Set(out.map((s) => JSON.stringify(s.config)));
    for (const place of row.places) {
      const key = JSON.stringify(place.server.config);
      if (seen.has(key)) continue;
      seen.add(key);
      out.push({
        key: `${place.machine.id ?? ''}:${place.agentId}`,
        label: t('mcp.machineConfig', place.machine.label),
        note: configSummary(place.server.config),
        config: place.server.config,
      });
    }
    return out;
  }, [row]);
  const [sourceKey, setSourceKey] = useState(sources[0]?.key ?? '');
  const source = sources.find((s) => s.key === sourceKey) ?? sources[0];

  /** A target is a machine and a writable agent on it, keyed for the checkbox set. */
  const targets = useMemo(
    () =>
      columns.flatMap((machine) =>
        (machine.listing?.agents ?? [])
          .filter((a) => a.writable)
          .map((agent) => {
            const info = machine.agents.find((a2) => a2.id === agent.agent);
            return {
              key: `${machine.id ?? ''}${PLACE_KEY}${agent.agent}`,
              machine,
              agentId: agent.agent,
              label: info?.label ?? agent.agent,
              icon: info?.icon,
              has: agent.servers.some((s) => s.name === row.name),
            };
          }),
      ),
    [columns, row.name],
  );

  const [checked, setChecked] = useState<Set<string>>(() => new Set(targets.filter((t) => !t.has).map((t) => t.key)));
  const [phase, setPhase] = useState<InstallPhase | null>(null);
  const busy = isRunning(phase);

  const toggle = (key: string) => {
    if (busy) return;
    setChecked((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  };

  /** Runs the writes. `overwritten` narrows a retry to the targets that said "exists". */
  const run = async (overwritten?: Set<string>) => {
    if (!source) return;
    const keys = [...checked].filter((k) => !overwritten || overwritten.has(k));
    const byMachine = new Map<string | null, string[]>();
    for (const key of keys) {
      const [machineId, ...rest] = key.split(PLACE_KEY);
      const agentId = rest.join(PLACE_KEY);
      const list = byMachine.get(machineId || null) ?? [];
      list.push(agentId);
      byMachine.set(machineId || null, list);
    }
    setPhase({ running: true });
    const results = new Map<string, McpInstallOutcome & { machineId: string | null }>(overwritten ? doneOf(phase) ?? [] : []);
    for (const [machineId, agentIds] of byMachine) {
      try {
        const outcomes = await clientFor(machineId).mcpInstall({ targets: agentIds, name: row.name, config: source.config, overwrite: Boolean(overwritten) });
        for (const outcome of outcomes) results.set(`${machineId ?? ''}${PLACE_KEY}${outcome.agent}`, { ...outcome, machineId });
      } catch (e) {
        onError(e);
        for (const agent of agentIds) results.set(`${machineId ?? ''}${PLACE_KEY}${agent}`, { agent, status: 'error', error: e instanceof Error ? e.message : String(e), machineId });
      }
    }
    setPhase({ done: results });
    onDone();
  };

  const label = (outcome: McpInstallOutcome | undefined): { text: string; tone: 'ok' | 'warn' | 'bad' | 'none' } => {
    switch (outcome?.status) {
      case 'installed':
        return { text: t('skills.installed'), tone: 'ok' };
      case 'same':
        return { text: t('mcp.same'), tone: 'none' };
      case 'exists':
        return { text: t('mcp.differs'), tone: 'warn' };
      case 'error':
        return { text: t('team.failed'), tone: 'bad' };
      default:
        return { text: '', tone: 'none' };
    }
  };

  const done = doneOf(phase);
  const existsKeys = done ? new Set([...done.values()].filter((o) => o.status === 'exists').map((o) => `${o.machineId ?? ''}${PLACE_KEY}${o.agent}`)) : null;

  return (
    <DialogSurface className="skill-dialog skill-sync" labelledBy="mcp-install-title" onClose={onClose} busy={busy}>
      <div className="skill-dialog-head">
        <h2 id="mcp-install-title" className="skill-dialog-title">
          {t('mcp.installTitle', row.name)}
        </h2>
        <button className="icon-btn" aria-label={t('common.close')} onClick={onClose} disabled={busy}>
          <IconClose size={16} />
        </button>
      </div>

      <div className="skill-sync-scroll">
        <div className="skill-sync-group">
          <div className="skill-sync-label">{t('mcp.whichConfig')}</div>
          {sources.length === 0 && <p className="panel-empty">{t('mcp.noSources')}</p>}
          {sources.map((s) => (
            <label key={s.key} className={`skill-source${source === s ? ' picked' : ''}`}>
              <input type="radio" name={`mcp-source-${row.name}`} checked={source === s} disabled={busy || Boolean(done)} onChange={() => setSourceKey(s.key)} />
              <span className="skill-source-text">
                <span className="skill-source-machine">{s.label}</span>
                <span className="skill-source-path" title={s.note}>
                  {s.note}
                </span>
              </span>
            </label>
          ))}
        </div>

        <div className="skill-sync-group">
          <div className="skill-sync-label">{t('skills.to')}</div>
          {columns.map((column) => {
            const reachable = column.listing !== null;
            const chips = targets.filter((target) => target.machine === column);
            return (
              <div key={column.id ?? 'local'} className="skill-target-row">
                <span className="skill-target-machine">{column.label}</span>
                <span className="skill-target-agents">
                  {!reachable && <span className="skill-target-none">{t('skills.unreachableLater')}</span>}
                  {reachable && chips.map((target) => {
                    const outcome = done?.get(target.key);
                    const state = label(outcome);
                    return (
                      <button
                        key={target.key}
                        type="button"
                        className={`skill-target${checked.has(target.key) ? ' on' : ''}${outcome ? ` ${state.tone}` : ''}`}
                        disabled={busy || Boolean(done)}
                        aria-pressed={checked.has(target.key)}
                        title={target.has ? t('mcp.hasServer') : t('mcp.notYet')}
                        onClick={() => toggle(target.key)}
                      >
                        {target.icon ? <AgentMark path={target.icon} size={13} /> : null}
                        <span className="skill-target-name">{target.label}</span>
                        {outcome ? (
                          state.tone === 'ok' ? <IconCheck size={12} /> : <span className={`skill-target-state ${state.tone}`}>{state.text}</span>
                        ) : null}
                      </button>
                    );
                  })}
                  {reachable && chips.length === 0 && <span className="skill-target-none">{t('mcp.noTargets')}</span>}
                </span>
              </div>
            );
          })}
        </div>

        {done && (
          <div className="skill-sync-results">
            {[...done.values()].filter((o) => o.status === 'error').map((o) => (
              <p key={`err:${o.machineId}:${o.agent}`} className="skill-sync-error">
                {o.agent}：{o.error ?? t('skills.installFailed')}
              </p>
            ))}
          </div>
        )}
      </div>

      <div className="dialog-actions skill-sync-actions">
        {existsKeys && existsKeys.size > 0 ? (
          <>
            <button className="ghost" onClick={onClose}>
              {t('common.close')}
            </button>
            <button className="primary" onClick={() => void run(existsKeys)}>
              {t('skills.overwrite', existsKeys.size)}
            </button>
          </>
        ) : done ? (
          <button className="primary" onClick={onClose}>
            {t('step.done')}
          </button>
        ) : (
          <>
            <button className="ghost" onClick={onClose} disabled={busy}>
              {t('common.cancel')}
            </button>
            <button className="primary" disabled={busy || !source || checked.size === 0} onClick={() => void run()}>
              {busy ? t('mcp.installing') : t('mcp.installCount', checked.size)}
            </button>
          </>
        )}
      </div>
    </DialogSurface>
  );
}
