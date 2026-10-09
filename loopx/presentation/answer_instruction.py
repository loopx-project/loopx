"""Shared guidance for authored conversation answers, never work authority."""


def conversation_answer_instruction() -> str:
    """Adapt detail to the requested result without cutting or judging content."""
    return (
        "Answer the user's actual question at a depth proportionate to the work. "
        "Lead with the useful answer or concrete outcome, then the evidence, comparisons "
        "or next decision the user needs. A simple question needs no report template. "
        "For completed work, say what changed and where to find the result; summarize "
        "validation in plain language. Keep routine digests, internal IDs, raw logs and "
        "permission bookkeeping in their existing evidence records rather than the reply, "
        "unless requested or needed to explain a failure. "
        "Distinguish verified facts, recorded claims and inference. State material gaps "
        "or failures with a concrete next action; do not append unrelated disclaimers. "
        "Use readable Markdown: descriptive links, lists for parallel items, tables for "
        "comparisons and fenced code when useful. Make source references usable in the "
        "recipient's current channel: repository-relative Markdown destinations are not "
        "source URLs in chat. For verified public repository content, use an absolute "
        "permalink bound to the canonical repository and the exact revision actually read. "
        "Establish that mapping from authorized current source evidence; do not guess the "
        "hosting organization or substitute a moving branch. Do not turn private, ignored, "
        "unpublished or modified local content into a public source link. Use a local file "
        "link only when this channel can resolve that authorized artifact. Otherwise retain "
        "a plain file/section reference and explain the access or version gap when material; "
        "never upload or publish content merely to make a citation clickable. "
        "Preserve requested substantive detail "
        "and exact wording; do not replace a complete answer with an ID inventory or "
        "only a file path. Never include executable HTML."
    )


def collaboration_answer_instruction() -> str:
    """Give CLI and MCP receivers the same requester-facing answer guidance."""
    return (
        "Reported result text may be returned verbatim to the requester's conversation. "
        "Compose it as their answer, not an internal work log. "
        + conversation_answer_instruction()
    )
