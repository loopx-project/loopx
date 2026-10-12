export interface TestPythonOptions {
  env?: NodeJS.ProcessEnv;
  repoRoot?: string;
  platform?: NodeJS.Platform;
}

export function resolveTestPython(options?: TestPythonOptions): string;

export function testPythonCommand(code: string, options?: TestPythonOptions): {
  command: string;
  args: string[];
};
