export const REPO_ROOT: string;
export const DEFAULT_TEST_DIR: string;
export const DEFAULT_CONCURRENCY_MIN: number;
export const DEFAULT_CONCURRENCY_MAX: number;
export const HEAVY_LANE_CONCURRENCY: number;
export const HEAVY_CONTROL_PLANE_TEST_FILES: readonly string[];

export function resolveTestConcurrency(
  env?: NodeJS.ProcessEnv | Record<string, string | undefined>,
  availableParallelism?: number,
): number;

export function shouldEnableCompileCache(
  env?: NodeJS.ProcessEnv | Record<string, string | undefined>,
): boolean;

export function listDefaultControlPlaneTestFiles(repoRoot?: string): string[];

export function analyzeCliArgs(argv?: readonly string[]): {
  hasExplicitTestTargets: boolean;
  hasExplicitConcurrency: boolean;
};

export function buildNodeTestArgs(
  argv?: readonly string[],
  options?: {
    env?: NodeJS.ProcessEnv | Record<string, string | undefined>;
    availableParallelism?: number;
    repoRoot?: string;
  },
): string[];

export type ControlPlaneRunPlan =
  | {
      mode: "single";
      totalConcurrency: number;
      args: string[];
    }
  | {
      mode: "two_lane";
      totalConcurrency: number;
      heavyConcurrency: number;
      standardConcurrency: number;
      heavyFiles: string[];
      standardFiles: string[];
    };

export function buildControlPlaneRunPlan(
  argv?: readonly string[],
  options?: {
    env?: NodeJS.ProcessEnv | Record<string, string | undefined>;
    availableParallelism?: number;
    repoRoot?: string;
  },
): ControlPlaneRunPlan;

export function createScopedCompileCache(options?: {
  env?: NodeJS.ProcessEnv | Record<string, string | undefined>;
  tmpRoot?: string;
}): {
  cacheDir: string | null;
  cleanup: () => void;
};

export function runControlPlaneTests(
  argv?: readonly string[],
  options?: {
    env?: NodeJS.ProcessEnv | Record<string, string | undefined>;
    availableParallelism?: number;
    repoRoot?: string;
    tmpRoot?: string;
    execPath?: string;
  },
): Promise<{
  status: number | null;
  signal: NodeJS.Signals | null;
  cacheDir: string | null;
}>;
