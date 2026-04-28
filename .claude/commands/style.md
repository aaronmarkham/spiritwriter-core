# /style — Apply Aaron's Writing Voice

Detect the content type of the file(s) being written or edited, then apply the appropriate style variant. All variants share a base voice; the variant controls person, structure, and tone calibration.

## Usage

```
/style                  — auto-detect from file path and content
/style lesson           — workshop lesson / tutorial
/style docs             — technical documentation or API reference
/style blog             — blog post (first-person narrative)
/style spec             — specification / RFC / design doc
```

## Content Type Detection

When no type is specified, infer from file path:

| Path pattern | Type |
|---|---|
| `docs/workshop/*.md` | lesson |
| `docs/specs/*.md` | spec |
| `docs/*.md` | docs |
| `skills/*/SKILL.md` | docs (concise) |
| `README.md` | docs |
| `blog/**`, or user says "blog" | blog |

If the path doesn't match, examine the content structure (step-by-step with checklists = lesson, code-heavy with short prose = docs, narrative with "I" = blog).

---

## Base Style — Aaron's Writing DNA

These rules apply to every variant. This is the voice underneath everything.

### Voice

- **Active voice, always.** "The agent falls back to regex" not "regex is fallen back to by the agent."
- **Concrete over abstract.** Real tool names, real numbers, real scenarios. "rz-bin extracts DEX class names" not "a binary analysis tool can be used to examine compiled code."
- **Direct address.** Use "you" freely. The reader is a peer, not a student.
- **Confident and generous.** Share knowledge like someone who's been through it and wants to save the reader time. No hedging, no "it might be possible to perhaps consider."
- **Technically precise.** Never sacrifice accuracy for readability. Get both.

### Rhythm

- **Mix sentence lengths.** Short sentences punch. Longer ones carry explanation. Alternate them for momentum.
- **Parenthetical asides** add color without derailing — one or two per section, not every paragraph.
- **Transitions pull forward.** Each section should create momentum to the next. Rhetorical questions, "here's what that means in practice," or naming the deeper issue.

### Structure

- **Tables for structured comparisons.** 3+ items with attributes = table.
- **Code examples are first-class.** A 5-line code block beats a paragraph of explanation. Show, then explain.
- **Headers are scannable.** Someone skimming should get the document's arc from headers alone.

### Anti-patterns (never do these)

- **Marketing speak:** "leverage," "utilize," "empower," "seamless," "robust"
- **Corporate hedging:** "it may be advisable to consider," "depending on your use case"
- **Filler intros:** "In this section, we will discuss..." — just discuss it
- **Over-qualification:** "It's important to note that..." — just state it
- **Passive voice** (unless quoting an error message or API response)
- **False simplicity:** "simply," "just," "easily" — if it were simple, you wouldn't need docs
- **LLM tells:** "Let's dive in," "Here's the thing," "Without further ado"

---

## Variant: Lesson / Tutorial

**Person:** No first-person. No "I", "my", "we", "our." Use "you" for the reader. Imperative mood or plain declarative for author actions.

**Structure:**
- **Blockquote opener.** One sentence that captures the lesson's core insight. This is the thing the reader will remember.
- **"What you'll learn" — outcome-oriented.** State what the reader will be able to DO, not what the lesson "covers." One short paragraph, no bullet list.
- **Start with the failure.** Each lesson connects to a real problem — something that broke, a silent failure, a production incident. The fix follows naturally.
- **Checklists at end.** Actionable verification items.
- **Troubleshooting section** with real error messages and real fixes.
- **Closing hook** that pulls the reader to the next lesson. Name the next problem concretely.

**Voice calibration:** Experienced practitioner at a conference workshop. Walking the room, answering questions, pointing at the screen. Authoritative but approachable. The reader should feel like they're getting insider knowledge, not reading a textbook.

**Do:**
- "This is what fails when the PATH is wrong" (direct, concrete)
- "The agent's instinct is to find a workaround. For dev work, that's fine. For security analysis, it's a disaster." (punchy contrast)
- "32 GPUs, 90 minutes. 256 GPUs, under 15." (numbers as story)

**Don't:**
- "In this lesson, you will learn how to..." (filler)
- "It is recommended that users verify..." (passive, corporate)
- "This can be a common source of errors." (vague, hedging)

---

## Variant: Documentation

**Person:** No first-person.

**Structure:**
- **Code-forward.** Lead with code examples, follow with explanation. The reader came to copy-paste and understand, in that order.
- **Concise prose.** 2-4 sentences between code blocks. One idea per paragraph.
- **Progressive disclosure.** Quick Start -> Core Concepts -> Advanced Usage -> API Reference.
- **Link generously.** Cross-reference related docs instead of re-explaining.
- **No narrative arc.** Docs are reference material. The reader may land on any section directly.

**Voice calibration:** Well-organized README that respects the reader's time. Terse but not cryptic. Every sentence earns its place.

---

## Variant: Blog

**Person:** First-person welcome. "I", "my", "we" are all fine.

**Structure:**
- **Narrative arc.** Hook (problem, surprising result, question) -> Build (how it works, what was tried) -> Land (call to action, forward look).
- **Personal experience.** Real incidents > hypotheticals. "This blew up in production" > "one might encounter issues."
- **Pop culture and humor** where natural. Don't force it, but don't suppress it.
- **Rhetorical questions as transitions.** They pull the reader forward.
- **Numbers tell stories.** "15 minutes on 256 GPUs" is more compelling than "significantly faster."
- **Self-deprecating honesty.** "I already had my preprocessed copy sitting around" builds trust.

**Voice calibration:** Aaron's AWS blog posts. A smart friend at a whiteboard with 8 years of technical writing experience, explaining something complex to a capable peer. Personality is a feature. The writing should be enjoyable even if you don't need the information yet.

---

## Variant: Specification

**Person:** No first-person.

**Structure:**
- **RFC-style keywords** where needed (MUST, SHOULD, MAY) with plain-English explanation.
- **Schema-first.** Define data structures early, explain behavior after.
- **Exhaustive.** Cover edge cases. An ambiguous spec is worse than no spec.
- **Minimal personality.** Implementable by someone who's never met Aaron.

**Voice calibration:** Clear, precise, complete. The reader is implementing against this document.

---

## Applying the Style

When editing existing content:

1. **Read the file first.** Understand the technical substance before touching the prose.
2. **Preserve accuracy.** Style changes must not alter technical meaning. If unsure, leave it.
3. **Don't over-edit.** If a passage already matches the target style, leave it alone. The goal is consistency, not rewriting for its own sake.
4. **Watch for LLM tells.** Generated content often has flat transitions, stock phrases, and over-qualified statements. These are the highest-value edits.
5. **Check cross-references.** If you change a heading, update any links pointing to it.
