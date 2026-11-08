import {useEffect, useState} from "react";
import {fetchResearchHistoryTiming, type ResearchHistoryTiming} from "../../data/chat";
import {observedLiveTurnClock} from "./goal-activity";
import type {WorkspaceGoal} from "./personal-workspace-model";

export type ResearchChildGoal = Pick<WorkspaceGoal, "goalId" | "title" | "sourceAccess" |
  "activationState" | "state" | "execution" | "hostThreadActivity">;

type SourceTarget = [goalId: string, originalGoalId: string, namespace: string | null];
type TimingRead = {targets: SourceTarget[]; directoryUrl?: string; refreshRevision: number};

function useChildTiming(goals: ResearchChildGoal[], directoryUrl: string | undefined, refreshRevision: number, active: boolean) {
  const request: TimingRead = {
    targets: goals.map(({goalId, sourceAccess}) =>
      [goalId, sourceAccess?.original_goal_id ?? goalId, sourceAccess?.namespace ?? null]),
    directoryUrl, refreshRevision,
  };
  // Status polling can reconstruct the same directory. Only identity or a
  // deliberate refresh should restart these reads.
  const key = JSON.stringify(request);
  const [snapshot, setSnapshot] = useState<{key: string; byGoal: Map<string, ResearchHistoryTiming | null>}>();
  useEffect(() => {
    if (!active) return;
    const {targets, directoryUrl} = JSON.parse(key) as TimingRead;
    const controller = new AbortController();
    const pending = targets.values();
    async function read() {
      for (const [goalId, originalGoalId, namespace] of pending) {
        if (controller.signal.aborted) return;
        const timing = await fetchResearchHistoryTiming(goalId, originalGoalId, namespace, directoryUrl, controller.signal)
          .catch(() => null);
        if (controller.signal.aborted) return;
        setSnapshot(previous => ({
          key,
          byGoal: new Map(previous?.key === key ? previous.byGoal : undefined).set(goalId, timing),
        }));
      }
    }
    // Keep a populated directory responsive without opening every source at once.
    for (let worker = 0; worker < Math.min(targets.length, 3); worker++) void read();
    return () => controller.abort();
  }, [active, key]);
  return snapshot?.key === key ? snapshot.byGoal : undefined;
}

const beijingClock = new Intl.DateTimeFormat("sv-SE", {
  dateStyle: "short", timeStyle: "short", timeZone: "Asia/Shanghai",
});

function Clock({value}: {value: string}) {
  return <time dateTime={value}>{beijingClock.format(new Date(value))}</time>;
}

function spanText(ms: number, zh: boolean) {
  const minutes = Math.floor(ms / 60_000);
  if (minutes === 0) return zh ? "不足 1 分钟" : "under 1 min";
  const hours = Math.floor(minutes / 60);
  const remainder = minutes % 60;
  return [hours > 0 && (zh ? `${hours} 小时` : `${hours} hr`),
    remainder > 0 && (zh ? `${remainder} 分钟` : `${remainder} min`)].filter(Boolean).join(" ");
}

function Timing({goal, timing, zh}: {goal: ResearchChildGoal; timing?: ResearchHistoryTiming | null; zh: boolean}) {
  const now = Date.now();
  const live = observedLiveTurnClock(goal, now);
  if (live?.startedAt) {
    return <><span>{zh ? "回合开始" : "Turn started"} <Clock value={live.startedAt}/></span>
      <span>{zh ? "进行中" : "In progress"} · {spanText(now - Date.parse(live.startedAt), zh)}</span></>;
  }
  if (live) {
    return <><span>{zh ? "最近活动" : "Last activity"} <Clock value={live.lastActivityAt}/></span>
      <span>{zh ? "进行中" : "In progress"}</span></>;
  }
  if (timing === undefined) return zh ? "读取时间…" : "Reading time…";
  if (timing === null) return zh ? "无法读取时间" : "Time unavailable";
  const {first_recorded_at: start, last_recorded_at: end, untimed_record_count: untimed, strategy_recorded_at: strategy} = timing;
  if (start && end) {
    const label = untimed ? (zh ? "已知记录" : "Dated records") : (zh ? "研究记录" : "Research records");
    if (start === end) return <span>{label} <Clock value={start}/></span>;
    return <><span>{label} <Clock value={start}/></span>
      <span>{zh ? "至" : "to"} <Clock value={end}/></span>
      <span>{zh ? "记录跨度" : "Recorded span"} {spanText(Date.parse(end) - Date.parse(start), zh)}</span></>;
  }
  if (strategy) return <span>{zh ? "策略记录" : "Saved strategy"} <Clock value={strategy}/></span>;
  return untimed ? (zh ? "记录时间未知" : "Record time unknown") : (zh ? "暂无时间记录" : "No dated records");
}

export function ResearchChildGoals({goals, directoryUrl, refreshRevision, active, showTiming, zh, onSelect}: {
  goals: ResearchChildGoal[]; directoryUrl?: string; refreshRevision: number; active: boolean; showTiming: boolean; zh: boolean;
  onSelect: (goalId: string) => void;
}) {
  const timing = useChildTiming(goals, directoryUrl, refreshRevision, active && showTiming);
  return <>
    <div className="research-child-heading">
      <h2>{zh ? "子 Goal 研究" : "Research in child Goals"}</h2>
      {showTiming ? <span>{zh ? "北京时间" : "Beijing time (UTC+8)"}</span> : null}
    </div>
    <div className="research-child-goals">{goals.map(goal => <button type="button" key={goal.goalId} onClick={() => onSelect(goal.goalId)}>
      <span className="research-child-body">
        <span>{goal.title}</span>
        {showTiming ? <span className="research-child-timing"><Timing goal={goal} timing={timing?.get(goal.goalId)} zh={zh}/></span> : null}
      </span>
      <span aria-hidden="true">↗</span>
    </button>)}</div>
  </>;
}
