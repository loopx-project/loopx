import {
  ArrowLeft,
  ArrowUpRight,
  CheckCircle2,
  ExternalLink,
  FlaskConical,
  GitCompareArrows,
  Search,
  ShieldCheck,
} from "lucide-react";
import { useMemo, useState } from "react";
import { usePublicPageNavigation } from "./usePublicPageNavigation";
import study from "../../../../benchmark/LHTB/studies/five-arm-gpt56sol-max/data.json";
import taskGroups from "../../../../benchmark/LHTB/studies/five-arm-gpt56sol-max/task-groups.json";
import copy from "./lhtb-copy.json";

type ArmKey = keyof typeof study.arms;
type Baseline = "plain" | "native_goal";
type TableMode = "all" | Baseline;
type GroupKey = keyof typeof taskGroups.groups;
const groupKeys = Object.keys(taskGroups.groups) as GroupKey[];
const taskGroup = new Map(groupKeys.flatMap((key) => taskGroups.groups[key].map((task) => [task, key] as const)));

function promptUrl(task: string) {
  const aliases: Record<string, string> = taskGroups.prompt_aliases;
  return `${taskGroups.prompt_base_url}${aliases[task] ?? task}/instruction.md`;
}

const primaryArms: ArmKey[] = ["plain", "native_goal", "new_heartbeat"];
const historicalArms: ArmKey[] = ["ssh_goal", "legacy_heartbeat"];
const baselines: Baseline[] = ["plain", "native_goal"];
// Derive all three metrics from the same effective task-arm cells.
const armMetrics = Object.fromEntries((Object.keys(study.arms) as ArmKey[]).map((arm) => {
  const rewards = study.tasks.map((row) => row[arm]);
  return [arm, {
    mean: rewards.reduce((sum, reward) => sum + reward, 0) / rewards.length,
    strict: rewards.filter((reward) => reward >= study.benchmark.solved_threshold).length,
    over80: rewards.filter((reward) => reward > 0.8).length,
  }];
})) as Record<ArmKey, { mean: number; strict: number; over80: number }>;

function formatRate(count: number) {
  return `${(count / study.tasks.length * 100).toFixed(1)}%`;
}
function compareTasks(tasks: typeof study.tasks, baseline: Baseline) {
  const deltas = tasks.map((row) => row.new_heartbeat - row[baseline]);
  const meanDelta = deltas.reduce((sum, delta) => sum + delta, 0) / deltas.length;
  const baselineMean = tasks.reduce((sum, row) => sum + row[baseline], 0) / deltas.length;
  return {
    baseline, meanDelta, relativeGain: meanDelta / baselineMean,
    wins: deltas.filter((delta) => delta > 0).length,
    ties: deltas.filter((delta) => delta === 0).length,
    losses: deltas.filter((delta) => delta < 0).length,
  };
}
const comparisons = baselines.map((baseline) => compareTasks(study.tasks, baseline));
const groupComparisons = groupKeys.map((key) => {
  const tasks = study.tasks.filter((row) => taskGroup.get(row.task) === key);
  return { key, count: tasks.length, comparisons: baselines.map((baseline) => compareTasks(tasks, baseline)) };
});

const contributorLinks = [
  { label: "@shangzh0", href: "https://github.com/shangzh0" },
  { label: "@gwh6669999", href: "https://github.com/gwh6669999" },
  { label: "Bouwen Zhou", href: "https://bouwenzhou.github.io/" },
  { label: "Wanli Lee", href: "https://wanli-lee.github.io/" },
] as const;

const studyUrl = "https://github.com/loopx-project/loopx/tree/main/benchmark/LHTB";

function formatMillions(value: number) {
  return `${(value / 1_000_000).toFixed(2)}M`;
}

function formatReward(value: number) {
  return value.toFixed(4);
}

export function LhtbBrief() {
  const [language, setLanguage] = usePublicPageNavigation("lhtb");
  const [query, setQuery] = useState("");
  const [tableMode, setTableMode] = useState<TableMode>("all");
  const [group, setGroup] = useState<GroupKey | "all">("all");
  const [showHistory, setShowHistory] = useState(false);
  const c = copy[language];
  const visibleArms = showHistory ? [...primaryArms, ...historicalArms] : primaryArms;
  const basePath = import.meta.env.BASE_URL;

  const visibleTasks = useMemo(() => {
    const normalized = query.trim().toLowerCase();
    return study.tasks.filter((row) => {
      if (group !== "all" && taskGroup.get(row.task) !== group) return false;
      if (normalized && !row.task.toLowerCase().includes(normalized)) return false;
      if (tableMode !== "all") return Math.abs(row.new_heartbeat - row[tableMode]) >= 0.05;
      return true;
    });
  }, [query, tableMode, group]);

  const resetFilters = () => { setQuery(""); setTableMode("all"); setGroup("all"); };

  const summaryTable = (arms: ArmKey[]) => (
    <div className="bm-table-wrap lhtb-summary-table">
      <table>
        <thead><tr>{c.summaryColumns.map((label) => <th scope="col" key={label}>{label}</th>)}</tr></thead>
        <tbody>{arms.map((arm) => {
          const row = armMetrics[arm];
          return (
            <tr className={arm === "new_heartbeat" ? "is-highlight" : undefined} key={arm}>
              <th scope="row"><code>{c.armLabels[arm]}</code><span>{c.armKinds[arm]}</span></th>
              <td><strong>{formatReward(row.mean)}</strong></td>
              {[row.strict, row.over80].map((count, index) => (
                <td key={index}><strong>{formatRate(count)}</strong><small>{count}/{study.tasks.length}</small></td>
              ))}
            </tr>
          );
        })}</tbody>
      </table>
    </div>
  );

  const caseCards = (cases: string[][]) => cases.map(([task, note]) => {
    const row = study.tasks.find((item) => item.task === task)!;
    return (
      <article key={task}>
        <h3>{task}</h3>
        <dl className="lhtb-case-scores">{primaryArms.map((arm) => (
          <div key={arm}><dt>{c.armLabels[arm]}</dt><dd>{formatReward(row[arm])}</dd></div>
        ))}</dl>
        <p>{note}</p>
        <a href={promptUrl(task)} target="_blank" rel="noopener" referrerPolicy="strict-origin-when-cross-origin">{c.promptLink} <ExternalLink size={11} /></a>
      </article>
    );
  });

  return (
    <div className="bm-page lhtb-page" id="top">
      <header className="bm-topbar">
        <a href={`${basePath}${language === "zh" ? "?lang=zh" : ""}`} className="bm-home-link">
          <ArrowLeft size={14} /> {c.back}
        </a>
        <span className="bm-edition">RESEARCH BRIEF / LHTB</span>
        <div className="bm-top-actions">
          <div className="bm-language" aria-label="Language">
            <button aria-pressed={language === "en"} className={language === "en" ? "is-active" : ""} onClick={() => setLanguage("en")} type="button">EN</button>
            <button aria-pressed={language === "zh"} className={language === "zh" ? "is-active" : ""} onClick={() => setLanguage("zh")} type="button">中文</button>
          </div>
          <a href={studyUrl} target="_blank" rel="noopener" referrerPolicy="strict-origin-when-cross-origin">
            {c.source} <ExternalLink size={13} />
          </a>
        </div>
      </header>

      <main>
        <section className="bm-hero bm-shell lhtb-hero">
          <div className="bm-hero-copy">
            <p className="bm-eyebrow"><FlaskConical size={14} /> {c.meta}</p>
            <h1>{c.title}</h1>
            <p className="bm-deck">{c.deck}</p>
            <div className="bm-hero-meta">
              <span className="bm-evidence-tag"><ShieldCheck size={14} /> {c.evidenceTag}</span>
              <p className="bm-contributors">
                <span>{c.contributorsLabel}</span>
                {contributorLinks.map((person) => (
                  <a href={person.href} key={person.href} target="_blank" rel="noopener" referrerPolicy="strict-origin-when-cross-origin">
                    {person.label}<ExternalLink size={11} />
                  </a>
                ))}
              </p>
            </div>
          </div>
        </section>

        <nav className="bm-reading-path bm-shell" aria-label={c.readingLabel}>
          {c.readingPath.map(([number, label, href]) => (
            <a href={href} key={href}><span>{number}</span><b>{label}</b></a>
          ))}
        </nav>

        <section className="bm-section bm-shell bm-summary" id="result">
          <div className="bm-section-lead bm-section-lead-wide">
            <p className="bm-kicker">{c.resultEyebrow}</p>
            <h2>{c.resultTitle}</h2>
            <p>{c.resultBody}</p>
          </div>
          {summaryTable(primaryArms)}
          <p className="bm-runner-note">{c.metricDefinitions}</p>
          <div className="lhtb-comparisons">
            {comparisons.map((row) => (
              <article key={row.baseline}>
                <h3>{c.comparisonTitle.replace("{baseline}", c.armLabels[row.baseline])}</h3>
                <strong>+{row.meanDelta.toFixed(4)}</strong><span>{c.deltaMean}</span>
                <p>+{(row.relativeGain * 100).toFixed(1)}% {c.relativeGain}</p>
                <p>{c.pairCounts.replace("{wins}", String(row.wins)).replace("{ties}", String(row.ties)).replace("{losses}", String(row.losses))}</p>
                <p>{c.pairSolves.replace("{current}", formatRate(armMetrics.new_heartbeat.strict)).replace("{baseline}", c.armLabels[row.baseline]).replace("{base}", formatRate(armMetrics[row.baseline].strict))}</p>
                <p>{c.pairOver80.replace("{current}", formatRate(armMetrics.new_heartbeat.over80)).replace("{baseline}", c.armLabels[row.baseline]).replace("{base}", formatRate(armMetrics[row.baseline].over80))}</p>
              </article>
            ))}
          </div>
          <p className="bm-runner-note">{c.comparisonNote}</p>
          <p className="bm-runner-note">{c.thresholdNote}</p>
          <p className="bm-runner-note"><strong>{c.readingNoteLabel}</strong>{c.readingNote}</p>
          <details className="lhtb-history"><summary>{c.historyTitle}</summary><p>{c.historyBody}</p>{summaryTable(historicalArms)}</details>
          <details className="lhtb-history">
            <summary>{c.costTitle}</summary>
            <div className="bm-table-wrap lhtb-cost-table"><table>
              <thead><tr>{c.costColumns.map((label) => <th scope="col" key={label}>{label}</th>)}</tr></thead>
              <tbody>{[...primaryArms, ...historicalArms].map((arm) => {
                const row = study.arms[arm];
                const tokens = "tokens" in row ? formatMillions(row.tokens)
                  : `${formatMillions(row.input_tokens)} in / ${formatMillions(row.output_tokens)} out`;
                const cost = "estimated_cost_usd" in row ? row.estimated_cost_usd : row.recorded_cost_usd;
                return <tr key={arm}><th scope="row">{c.armLabels[arm]}</th><td>{tokens}</td><td>${cost.toFixed(2)}</td></tr>;
              })}</tbody>
            </table></div>
          </details>
        </section>

        <section className="bm-section bm-shell" id="benchmark">
          <div className="bm-section-lead">
            <p className="bm-kicker">{c.benchmarkEyebrow}</p>
            <h2>{c.benchmarkTitle}</h2>
            <p>{c.benchmarkBody}</p>
          </div>
          <div className="lhtb-official-links">
            {c.officialLinks.map(([label, body, href]) => (
              <a href={href} key={href} target="_blank" rel="noopener" referrerPolicy="strict-origin-when-cross-origin">
                <div><strong>{label}</strong><p>{body}</p></div><ArrowUpRight size={18} />
              </a>
            ))}
          </div>
          <div className="bm-fact-grid">
            {c.benchmarkFacts.map(([value, label, note]) => (
              <article key={label}><strong>{value}</strong><span>{label}</span><small>{note}</small></article>
            ))}
          </div>
          <div className="lhtb-taxonomy" id="categories">
            <h3>{c.categoriesTitle}</h3>
            <p>{c.categoriesBody} <a href="https://zli12321.github.io/LHTB/index.html#benchmark" target="_blank" rel="noopener" referrerPolicy="strict-origin-when-cross-origin">{c.categoriesSource}</a> · <a href="https://github.com/zli12321/LHTB#task-categories-46-tasks" target="_blank" rel="noopener" referrerPolicy="strict-origin-when-cross-origin">{c.categoriesExamplesSource}</a></p>
            <div className="bm-table-wrap lhtb-category-table">
              <table>
                <caption>{c.categoriesTitle}</caption>
                <thead><tr>{c.categoryColumns.map(label => <th scope="col" key={label}>{label}</th>)}</tr></thead>
                <tbody>{c.categories.map(([category, examples]) => (
                  <tr key={category}><th scope="row">{category}</th><td>{examples}</td></tr>
                ))}</tbody>
              </table>
            </div>
            <p>{c.taskExample}</p>
            <p className="bm-runner-note">{c.benchmarkSetupNote}</p>
          </div>
        </section>

        <section className="bm-section bm-shell" id="mechanisms">
          <div className="bm-section-lead bm-section-lead-wide">
            <p className="bm-kicker">{c.mechanismEyebrow}</p>
            <h2>{c.mechanismTitle}</h2>
            <p>{c.mechanismBody}</p>
          </div>
          <div className="lhtb-arm-flow">
            {c.mechanismRows.map(([key, title, owner, body], index) => (
              <article className={key === "new_heartbeat" ? "is-new" : undefined} key={key}>
                <span>0{index + 1}</span>
                <div><h3>{title}</h3><small>{owner}</small><p>{body}</p></div>
              </article>
            ))}
          </div>
        </section>

        <section className="bm-section bm-shell lhtb-insight-section" id="task-types">
          <div className="bm-section-lead bm-section-lead-wide" id="insights">
            <p className="bm-kicker">{c.insightEyebrow}</p>
            <h2>{c.insightTitle}</h2>
            <p>{c.insightBody}</p>
          </div>
          <div className="lhtb-group-analysis">
            <p>{c.groupMethod}</p>
            <div className="bm-table-wrap lhtb-group-table">
              <table>
                <caption>{c.groupCountLabel} · Δ Reward</caption>
                <thead><tr>{c.groupColumns.map((label) => <th scope="col" key={label}>{label}</th>)}</tr></thead>
                <tbody>{groupComparisons.map((row) => (
                  <tr key={row.key} data-group={row.key}>
                    <th scope="row"><a href="#scores" onClick={() => { resetFilters(); setGroup(row.key); }}>{c.groupLabels[row.key]}</a></th>
                    <td>{row.count}</td>
                    {row.comparisons.map((comparison) => (
                      <td key={comparison.baseline}>
                        <strong>{comparison.meanDelta > 0 ? "+" : ""}{comparison.meanDelta.toFixed(4)}</strong>
                        <small>{comparison.wins} / {comparison.ties} / {comparison.losses}</small>
                      </td>
                    ))}
                    <td>{c.groupNotes[row.key]}</td>
                  </tr>
                ))}</tbody>
              </table>
            </div>
            <details className="lhtb-history"><summary>{c.sensitivityTitle}</summary><p>{c.sensitivityBody}</p></details>
            <p className="bm-runner-note">{c.promptBoundary}</p>
          </div>
          <div className="bm-insight-grid lhtb-insight-grid">
            {c.insights.map(([title, body], index) => (
              <article key={title}><span>0{index + 1}</span><h3>{title}</h3><p>{body}</p></article>
            ))}
          </div>
          <details className="lhtb-history lhtb-case-details">
            <summary>{c.caseDetails}</summary>
            <div className="lhtb-cases">
              <div>
                <p className="bm-kicker">{c.gainTitle}</p>
                {caseCards(c.gainCases)}
              </div>
              <div>
                <p className="bm-kicker lhtb-caution">{c.lossTitle}</p>
                {caseCards(c.lossCases)}
              </div>
            </div>
          </details>
        </section>

        <section className="bm-section bm-shell" id="scores">
          <div className="bm-section-lead bm-section-lead-wide">
            <p className="bm-kicker">{c.scoresEyebrow}</p>
            <h2>{c.scoresTitle}</h2>
            <p>{c.scoresBody}</p>
          </div>
          <div className="lhtb-table-tools">
            <label><Search size={15} /><input aria-label={c.searchPlaceholder} value={query} onChange={(event) => setQuery(event.target.value)} placeholder={c.searchPlaceholder} /></label>
            <div className="lhtb-segments" aria-label={c.filterLabel}>
              {(["all", ...baselines] as TableMode[]).map((mode) => (
                <button aria-pressed={tableMode === mode} className={tableMode === mode ? "is-active" : undefined} onClick={() => setTableMode(mode)} type="button" key={mode}>
                  {c.filters[mode]}
                </button>
              ))}
            </div>
          </div>
          <div className="lhtb-group-tools">
            <label htmlFor="lhtb-group">{c.groupFilterLabel}</label>
            <select id="lhtb-group" value={group} onChange={(event) => setGroup(event.target.value as GroupKey | "all")}>
              <option value="all">{c.allGroups}</option>
              {groupKeys.map((key) => <option value={key} key={key}>{c.groupLabels[key]}</option>)}
            </select>
            <button type="button" onClick={resetFilters}>{c.resetFilters}</button>
          </div>
          <button className="lhtb-history-toggle" type="button" aria-pressed={showHistory} onClick={() => setShowHistory(!showHistory)}>{showHistory ? c.hideHistory : c.showHistory}</button>
          <div className="bm-table-wrap lhtb-task-table">
            <table>
              <thead><tr><th scope="col">{c.taskColumn}</th>{visibleArms.map((arm) => <th scope="col" key={arm}>{c.armLabels[arm]}</th>)}</tr></thead>
              <tbody>
                {visibleTasks.map((row) => {
                  const best = Math.max(...visibleArms.map((arm) => row[arm]));
                  return (
                    <tr key={row.task}>
                      <th scope="row"><a href={promptUrl(row.task)} target="_blank" rel="noopener" referrerPolicy="strict-origin-when-cross-origin"><code>{row.task}</code></a><span>{c.groupLabels[taskGroup.get(row.task)!]}</span></th>
                      {visibleArms.map((arm) => (
                        <td className={row[arm] === best ? "is-best" : undefined} key={arm}>{formatReward(row[arm])}</td>
                      ))}
                    </tr>
                  );
                })}
                {visibleTasks.length === 0 && <tr><td colSpan={visibleArms.length + 1}>{c.emptyTasks}</td></tr>}
              </tbody>
            </table>
          </div>
          <p className="lhtb-visible-count" role="status">{c.visibleCount.replace("{count}", String(visibleTasks.length))}</p>
        </section>

        <section className="bm-section bm-shell lhtb-program" id="program">
          <div>
            <p className="bm-kicker">{c.programEyebrow}</p>
            <h2>{c.programTitle}</h2>
            <p>{c.programBody}</p>
          </div>
          <div className="lhtb-program-map">
            {c.programSteps.map(([title, body], index) => (
              <article key={title}><span>{index + 1}</span><div><h3>{title}</h3><p>{body}</p></div></article>
            ))}
            <a href={c.programUrl} target="_blank" rel="noopener" referrerPolicy="strict-origin-when-cross-origin">{c.programAction}<ExternalLink size={14} /></a>
          </div>
        </section>

        <section className="bm-section bm-shell bm-boundary" id="limits">
          <div>
            <p className="bm-kicker">{c.limitsEyebrow}</p>
            <h2>{c.limitsTitle}</h2>
            <ul>{c.limits.map((item) => <li key={item}>{item}</li>)}</ul>
          </div>
          <aside>
            <GitCompareArrows size={24} />
            <h3>{c.nextTitle}</h3>
            <p>{c.nextBody}</p>
          </aside>
        </section>

        <section className="bm-section bm-shell" id="sources">
          <div className="bm-section-lead bm-section-lead-wide">
            <p className="bm-kicker">{c.sourcesEyebrow}</p>
            <h2>{c.sourcesTitle}</h2>
          </div>
          <div className="bm-source-list">
            {c.sourceItems.map(([title, body, href], index) => (
              <a href={href} target="_blank" rel="noopener" referrerPolicy="strict-origin-when-cross-origin" key={title}>
                <span>0{index + 1}</span><div><strong>{title}</strong><p>{body}</p></div><ExternalLink size={15} />
              </a>
            ))}
          </div>
          <p className="lhtb-attestation"><CheckCircle2 size={15} /> {c.attestation}</p>
        </section>
      </main>

      <footer className="bm-footer bm-shell"><span>LoopX / LHTB Research Brief</span><p>{c.footer}</p><a href="#top">{c.backTop}</a></footer>
    </div>
  );
}
