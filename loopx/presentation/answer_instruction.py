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
        "comparisons and fenced code when useful. Preserve requested substantive detail "
        "and exact wording; do not replace a complete answer with an ID inventory or "
        "only a file path. Never include executable HTML."
    )
