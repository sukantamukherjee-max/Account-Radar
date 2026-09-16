# Phase 1E — Evidence & Sales-Hypothesis Hardening

## Objective

Prevent discovery/query labels and generic keywords from being mistaken for
customer buying evidence while preserving useful current-state, incumbent,
stakeholder, and project context.

## Changes

- Search-family `technology` is no longer treated as proof that a publisher is
  an authoritative technology source.
- Third-party technographics / technology-stack pages are retained only as
  `weak_relevance` capability evidence and cannot become direct or strong.
- LinkedIn job pages are treated as weak capability evidence; they remain
  valuable as intent/stakeholder signals elsewhere in the report.
- Generic `integration` was removed from capability generic triggers. Specific
  `systems integration` remains a managed-services keyword.
- Generic-trigger strong inference now requires local association between the
  target organization and the matched generic trigger.
- Official/customer-specific technology events and procurement remain eligible
  for direct evidence under the existing deterministic rules.
- No organization, sector, country, or customer name is hard-coded.

## Intended commercial semantics

Current-state technology use is not equivalent to a buying requirement.
Incumbent evidence remains available for competitive positioning, while
sales-ready narratives are driven by customer-specific project/procurement/
technology signals.
