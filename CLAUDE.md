# CLAUDE.md

## Project context: Take-Home Assignment (AI in Finance)

This repo (ReconcileAI) is the prototype for a take-home assignment. The full brief is below — use it to guide design, code, and write-up decisions.

### Section 1 — Overview

Finance is one of the largest industries in the world and one of the most document-heavy: tax rules, audit evidence, regulations — and much of the work is still done by experts reading, checking and cross-referencing by hand. An engineer walks into that kind of environment, finds where AI can genuinely help, and builds something people trust.

**Job:** pick an area of finance, define one simple problem that exists, and design an AI-based solution to it.

Examples to **avoid** reusing as the problem:
1. Regulatory circulars → mapping to internal policies/processes and proving action was taken (compliance teams reading PDFs, updating spreadsheets).
2. Trade-agreement tariff savings unclaimed by small exporters due to complex rules-of-origin.

Pick any area (tax, audit, regulation, cross-border agreements, or something else) and find a problem of your own.

### Section 2 — Task

1. **Define one problem.** Why it matters and why now. State what is out of scope.
2. **Design the solution.** How it addresses the problem: what data goes in, what the AI does, what comes out, where human review is needed, how the system handles uncertainty or mistakes. Explain why AI is necessary instead of a simpler, non-AI approach.
3. **Prove it a little (optional).** Build the smallest thing that shows the idea works (prototype on a few public documents, notebook, or clickable mock). Test on **at least 10 examples you create**, and report honestly where it fails.

#### Architecture — weighted heavily
Not looking for the most complex system; want a design that fits the problem in a way others might not have thought of, with reasoning that holds up under challenge. Questions to answer:
- Is one LLM call enough, or does it need retrieval over source documents, a rules engine, a deterministic calculator, several cooperating agents, or a mix? Why that combination?
- Which parts must be exact and auditable (a tax amount, a cited clause) and which can be probabilistic? How are they kept apart?
- Where does the knowledge live: model, retrieval index, code, or a human? What happens when the knowledge is no longer valid?
- Can every output be traced back to its source so an auditor/regulator can check it?
- What is the weakest point of the design, and what would break it?
- What was considered and rejected, and why?

> A simple design with sharp reasoning beats an elaborate one you can't defend.

#### Appendix (one page): what was added beyond the AI
- **Where AI helped:** main uses, with 2–3 key prompts.
- **What was rejected or corrected:** suggestions that were wrong, generic, or unsafe for the domain, and how that was known.
- **What came from me:** decisions, insights, or design choices the AI did not suggest (e.g. learned from a real document or practitioner, a trade-off, a risk spotted).
- **One thing I'd stand behind without AI:** the part understood well enough to defend live with no tools.

### Section 3 — Submission

1. **Written brief (4 pages max)**, including:
   - the area explored and what was surprising;
   - problem statement: who, what task, how often, what it costs today;
   - what was added beyond the AI;
   - solution design, with an **architecture diagram** showing where human and AI each act, and alternatives rejected and why;
   - risks: wrong answers, outdated rules, confidential data, liability — and how the design handles each;
   - things to consider before spending more time on the problem.
2. **Prototype or mock (optional).** Link to repo/notebook/clickable mock with a **README** explaining how to run it and the examples tested. Link must be publicly accessible.
3. **LLM logs.** The conversation(s) with the LLM, uploaded as a zip.

### Guidelines
- **Data:** find it yourself. Public sources (tax treaties, government tax guides, regulator circulars, public annual reports, sample contracts). Make up realistic examples where needed and **say which are made up**.
- **No real client or personal financial data.** Public or synthetic only.
- **Finance experience not required** — show learning in the brief.
- **Jurisdiction:** any; state which one the solution covers and what would change for another.
- **Code:** as little as proves the point.
- **Models:** free OpenRouter tier is fine; keep tests small and **note the model used in the README**.
- **Tax/legal advice is a real constraint** — treat it as part of the design. Explain who is accountable for the output and how a qualified human stays in the loop where the law requires one.
