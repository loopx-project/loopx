// <!-- loopx-managed-slash-command:v1 command=/goal surface=opencode-plugin -->
import { tool } from "@opencode-ai/plugin"
import { GoalPlugin } from "opencode-goal-plugin"

import { createLoopxGoalPlugin } from "../loopx/goal-bridge-runtime.mjs"


export const LoopXGoalPlugin = createLoopxGoalPlugin({ GoalPlugin, tool })
