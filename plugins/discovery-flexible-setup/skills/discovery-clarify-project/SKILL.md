---
name: discovery-clarify-project
description: |
  Cross-domain / Research methodology and experiment design — Review the current
  Microsoft Discovery project's purpose, expected outcomes, and task list, then
  surface up to 5 targeted clarifying questions to the user via `/askQuestion`
  about ambiguities, gaps, or concerns before any work begins. Goal is to give
  the user a chance to reconsider scope, assumptions, or approach before kickoff.
  USE FOR: 'clarify this project', 'ask me clarifying questions before we start',
  'review the project and challenge assumptions', 'pre-flight the project plan',
  'what's ambiguous about this project'. DO NOT USE FOR: executing tasks,
  generating a plan from scratch, or reviewing completed work.
license: MIT
metadata:
  version: "1"
  category: "Cross-domain"
  subfield: "Research methodology and experiment design"
---

# discovery-clarify-project

Pre-flight a Microsoft Discovery project by surfacing clarifying questions
**before** any task is executed. This is a lightweight sanity check that
gives the user one final opportunity to reconsider scope, assumptions, or
approach.

## When to run

The user asks you to review, clarify, pre-flight, or challenge the current
project before getting started. Typical trigger phrases:

- "clarify this project"
- "ask me clarifying questions before we begin"
- "review the project and flag anything ambiguous"
- "any concerns before we start?"

## What to do

1. **Read the project context.** Load the current Discovery project's:
   - Purpose / problem statement
   - Expected outcomes / success criteria
   - Task list (planned steps, deliverables, or subtasks)

2. **Identify ambiguities and concerns.** Look specifically for:
   - Undefined success criteria or vague outcomes
   - Missing constraints (time, budget, data access, tools)
   - Task steps that assume prerequisites not listed in the project
   - Conflicts between stated purpose and the task list
   - Scope that appears too large, too small, or off-target
   - Unstated dependencies on other people, systems, or data
   - Ambiguous ownership or handoff points

3. **Surface up to 5 questions via `/askQuestion`.** Rules:
   - **Maximum 5 questions.** Fewer is better if the project is clear.
   - Each question must be **specific and actionable** — the user should be
     able to answer it in one or two sentences and have that answer change
     what you do next.
   - Prefer questions that could cause **course correction** over
     questions that only add detail.
   - Do NOT ask questions whose answers are already explicit in the project
     purpose, outcomes, or task list.
   - Do NOT ask stylistic or formatting questions.
   - If the project is fully clear and you have zero concerns, say so
     plainly and ask nothing.

4. **Do not start work.** This skill only clarifies. Wait for the user's
   answers before proceeding to execution.

## Output format

Brief lead-in (1–2 sentences) naming what you reviewed and how many
questions you have, then the `/askQuestion` calls. Nothing else.
