# Roadmap

**Status: sketch, not commitments.** This is a single-maintainer personal tool
that happens to be shareable. Nothing here is promised or dated — see the
support posture in [CONTRIBUTING.md](../CONTRIBUTING.md).

## Next

- Onboarding as an agent skill: `Scenario` field metadata, a profile `doctor`
  check, a `bootstrap-interview` skill.
- Married filing jointly, then additional states (no-tax → flat → bracketed).
- Income shapes beyond RSU tech: 1099/self-employment, 403(b)/457(b) +
  pension, K-1, private-company equity (ISOs/AMT), variable income.
- Household realities: dependents, HSA, rental property.
- Retirement-side depth: RMDs, IRMAA, Medicare/ACA seam at 65, richer Monte
  Carlo, a Roth conversion optimizer.
- Year-keyed bracket tables and the annual refresh routine
  (today's routine is described in [CONTRIBUTING.md](../CONTRIBUTING.md)).

## Deliberately out of scope

- **Hosting.** No accounts, no auth, no SaaS. "Your data never leaves your
  machine" is a real differentiator, and holding other people's financial
  data is a liability a single maintainer should not take on.
- **Feature parity with commercial planners.** This tool loses on breadth and
  wins on tax fidelity and hackability; chasing breadth trades away the only
  advantage.
- **Direct account aggregation.** The Monarch connector covers it; direct
  Plaid-style integration is a support burden with no matching payoff.

## What this is actually for

The differentiator isn't the tax math — it's that the tool is **agent-native**.
`variometer explain` as a deterministic, diffable output engine, plus `CLAUDE.md`,
plus `.claude/skills/`, plus `ad_hoc/*.py` with `--python`, means someone can
ask "what if I quit in 2029 and take two lean years" in English and get real
tax math back. Work that strengthens that loop generally wins ties against
work that doesn't.
