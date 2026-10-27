/** Source-reported finding meaning, shared by history and saved-result readers.
 * These labels do not classify evidence or schedule verification. */
export function exploreFindingCopy(zh: boolean) {
  return zh ? {
    labels: {tentative: "暂定结论", confirmed: "来源记为确认", refuted: "已反驳"},
    help: "结论状态说明",
    meaning: "暂定结论表示来源尚未确认或反驳该结论，可能已有观测与证据；不表示有检查任务排队。确认与反驳也仅表示来源在所述适用范围内的判断，不等于独立验证或目标完成。研究问题是否收束、下一步继续或转向，分别记录。",
  } : {
    labels: {tentative: "Tentative", confirmed: "Recorded confirmation", refuted: "Refuted"},
    help: "About finding states",
    meaning: "Tentative means the source has not confirmed or refuted the finding; observations and evidence may already exist. It does not mean a check is queued. Confirmation and refutation are source judgments within the stated scope, not independent verification or Goal completion. Question closure and the decision to continue or replan are recorded separately.",
  };
}
