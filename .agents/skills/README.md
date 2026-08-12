# Matt Pocock Skills

This directory contains the 25 stable skills published by
[`mattpocock/skills`](https://github.com/mattpocock/skills) at commit
`84fdeffd12f2ee307994d1eb6feb48173b6e0502` (2026-08-06).

The install intentionally includes the upstream `engineering` and `productivity`
skills advertised by the repository's plugin manifest. It excludes `in-progress`
skills and the optional `misc` collection.

## Usage

- Start a new Codex task in this repository, or use `/skills` to refresh and list
  available skills.
- Invoke a skill explicitly with `$skill-name`, for example `$diagnosing-bugs`,
  `$tdd`, `$code-review`, or `$grill-me`.
- Codex may also select model-invoked skills automatically when their description
  matches the request.
- Before relying on issue-oriented skills such as `$triage`, `$to-spec`, or
  `$to-tickets`, run `$setup-matt-pocock-skills` once and approve its proposed
  repository configuration.

The skills are project-scoped: they apply when Codex works in this repository and
do not install Python or production runtime dependencies.

Fourteen user-invoked skills also carry upstream's cross-client
`disable-model-invocation` frontmatter. Codex enforces the equivalent behavior
through each skill's `agents/openai.yaml` setting
`policy.allow_implicit_invocation: false`; keep those two declarations aligned
when updating. The other eleven skills may be selected automatically.

## Installed skills

Engineering:

- `ask-matt`
- `code-review`
- `codebase-design`
- `diagnosing-bugs`
- `domain-modeling`
- `grill-with-docs`
- `implement`
- `improve-codebase-architecture`
- `prototype`
- `research`
- `resolving-merge-conflicts`
- `setup-matt-pocock-skills`
- `tdd`
- `to-spec`
- `to-tickets`
- `triage`
- `wayfinder`
- `wizard`

Productivity:

- `grill-me`
- `grilling`
- `handoff`
- `teach`
- `to-questionnaire`
- `wait-what`
- `writing-for-agents`

## Updating

Review upstream changes before replacing the committed copies. Re-run the Codex
skill installer with the same 25 source paths and this directory as `--dest`, or
install into a temporary directory and compare it with the committed version.
Preserve the upstream license notice in
`LICENSE.mattpocock-skills`.
