---
name: Using {{name}} for typical tasks
description: When and how to reach for {{name}}'s tools, with concrete usage tips for common research workflows.
# bundle: <name>   # optional: scope this skill to a bundle (surfaced only when that bundle is available)
---

# Using {{name}}

This skill is a guide for *you*, the agent, on how to use this toolkit effectively
when a user asks for help with the kind of work it supports. Replace this content
with guidance specific to your toolkit.

## When to use this toolkit

Reach for `{{name}}` when the user wants to:

- (Replace with a representative task from your domain.)
- (Replace with another representative task.)
- (Replace with one more.)

Avoid using `{{name}}` for tasks unrelated to its domain — even if the toolkit's
tool names sound applicable, the schemas are tuned for specific inputs.

## Common workflow

A typical session looks like:

1. Call the toolkit's primary tool with the user's query (use sensible defaults
   for parameters they didn't specify).
2. Summarize the result in plain prose for the user.
3. If the user asks for a deeper look at a specific item, call the toolkit's
   detail-fetching tool with that item's identifier.

## Tips that matter

- The tools return JSON strings — parse before quoting fields, don't grep.
- Surface only the fields the user asked for; raw payloads are noisy.
- When confidence is low, tell the user explicitly rather than padding with
  filler. Users prefer "I'm not sure, here's what I found" over
  fabricated certainty.

## What to skip

- Don't chain more than 2–3 calls without checking in with the user.
- Don't reformat numeric data (rounding, unit conversion) without saying so.
