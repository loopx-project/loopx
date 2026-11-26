import assert from "node:assert/strict";
import test from "node:test";

import {selectPrReviewConclusion} from "../../loopx/control_plane/capabilities/pr_review_approval_closeout.ts";

const HEAD = "a".repeat(40);
const OLD = "b".repeat(40);

function makeOpinion(
  reviewId: number,
  {
    login = "loopx-agent",
    state = "COMMENTED",
    head = HEAD,
    time = reviewId,
    association = "MEMBER",
    isBot = false,
    valid = false,
    verdict = "COMMENT",
    threadReplyOnly = false,
    family = null as string | null,
    reviewBodyValid = false,
  } = {},
) {
  return {
    state,
    login,
    head,
    time,
    review_id: reviewId,
    url: `https://github.com/loopx-project/loopx/pull/9038#pullrequestreview-${reviewId}`,
    association,
    is_bot: isBot,
    submitted_at: `2026-11-26T21:16:${String(reviewId).padStart(2, "0")}Z`,
    conclusion: {valid, verdict, reviewer: login, state},
    ...(threadReplyOnly ? {thread_reply_only: true} : {}),
    ...(family !== null ? {review_model_family: family, review_body_valid: reviewBodyValid} : {}),
  };
}

test("thread-reply-only COMMENTED wrapper on PR opened by another author does not establish independent_review", () => {
  const result = selectPrReviewConclusion({
    author: "cocolord",
    head: HEAD,
    reviews_complete: true,
    include_independent_coverage: true,
    reviews: [
      makeOpinion(6, {login: "loopx-agent", state: "COMMENTED", head: HEAD, threadReplyOnly: true}),
      makeOpinion(4, {login: "loopx-agent", state: "CHANGES_REQUESTED", head: OLD, verdict: "REQUEST_CHANGES"}),
    ],
  });
  assert.equal(result.independent_review, undefined);
  assert.deepEqual(result.independent_review_activity_ids, []);
  assert.deepEqual(result.independent_coverage_review_ids, []);
});

test("later thread-reply-only COMMENTED wrapper does not overwrite earlier current-head opinion", () => {
  const priorFormal = makeOpinion(10, {
    login: "loopx-agent",
    state: "CHANGES_REQUESTED",
    head: HEAD,
    valid: true,
    verdict: "REQUEST_CHANGES",
    family: "gpt",
    reviewBodyValid: true,
  });
  const replyWrapper = makeOpinion(12, {
    login: "loopx-agent",
    state: "COMMENTED",
    head: HEAD,
    threadReplyOnly: true,
  });

  for (const ordered of [[priorFormal, replyWrapper], [replyWrapper, priorFormal]]) {
    const result = selectPrReviewConclusion({
      author: "cocolord",
      head: HEAD,
      reviews_complete: true,
      include_independent_coverage: true,
      reviews: ordered,
    });
    assert.deepEqual(result.independent_review, {
      reviewer: "loopx-agent",
      association: "MEMBER",
      state: "CHANGES_REQUESTED",
      head_oid: HEAD,
      review_id: 10,
      url: "https://github.com/loopx-project/loopx/pull/9038#pullrequestreview-10",
      submitted_at: "2026-11-26T21:16:10Z",
      source_complete: true,
      grants_approval: false,
      grants_merge_authority: false,
    });
    assert.deepEqual(result.independent_review_activity_ids, [10]);
    assert.deepEqual(result.independent_coverage_review_ids, [10]);
  }
});

test("genuine inline-root COMMENTED review without thread_reply_only still establishes independent_review", () => {
  const result = selectPrReviewConclusion({
    author: "cocolord",
    head: HEAD,
    reviews_complete: true,
    reviews: [
      makeOpinion(1, {login: "contributor", association: "CONTRIBUTOR", state: "COMMENTED", head: HEAD}),
    ],
  });
  assert.equal((result.independent_review as {review_id: number}).review_id, 1);
  assert.equal((result.independent_review as {state: string}).state, "COMMENTED");
});

test("qualified COMMENTED conclusion is preserved even if thread_reply_only flag is present", () => {
  const result = selectPrReviewConclusion({
    author: "cocolord",
    head: HEAD,
    reviews_complete: true,
    reviews: [
      makeOpinion(7, {
        login: "peer",
        state: "COMMENTED",
        head: HEAD,
        valid: true,
        verdict: "APPROVE",
        threadReplyOnly: true,
      }),
    ],
  });
  assert.equal((result.independent_review as {review_id: number}).review_id, 7);
});
