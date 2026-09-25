// Parses the explicit resume-condition field in the Todo editor. Not a chat router.
export function parseTodoResumeCondition(rawMessage: string) {
  const message = rawMessage.replace(/\s+/gu, " ").trim().toLowerCase();
  const match = message.match(
    /(?:^|[\s，,；;：（(:]|到|至)(?<condition>todo_done:todo_[a-z0-9_-]{3,64}|pr_merged:(?:(?:[a-z0-9_.-]{1,80})\/(?:[a-z0-9_.-]{1,100}))?#[1-9][0-9]{0,8}|capacity_available:[a-z][a-z0-9_:-]{0,63}|resume_at:[1-9][0-9]{3}-[0-9]{2}-[0-9]{2}t[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,3})?(?:z|[+-][0-9]{2}:[0-9]{2}))(?=$|[\s，,。；;）)])/iu,
  );
  return match?.groups?.condition ?? null;
}
