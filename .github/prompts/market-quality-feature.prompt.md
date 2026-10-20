---
name: Market-quality feature workflow
description: "Run a bounded feature through Product Owner, Developer, Tester, and applicable UX, security/privacy, and release reviews."
agent: Product Delivery Lead
argument-hint: "Describe the user problem or feature to deliver"
---

Deliver this request as one verifiable Training Planner milestone:

${input:feature:Describe the user problem or feature}

Follow the Product Owner → Developer → Tester sequence and the guardrails in `AGENTS.md`. Consult Product Designer for significant UX changes, Security & Privacy Reviewer for identity/sharing/secrets/integrations/AI/personal data, and Release Manager for deployment or release-readiness work.

Begin by inspecting the existing implementation and tests. Keep scope bounded, preserve existing user data and unrelated work, and add acceptance-focused regression tests. Do not use real passwords or production records, deploy, push, or claim market readiness without evidence. Finish with the actual tests, diagnostics, review results, limitations, and the next highest-value backlog items.
