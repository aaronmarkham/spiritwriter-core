# ToorCamp 2026 CFP Submission — DRAFT

**Submission target:** April 1, 2026 (first deadline) / May 4, 2026 (second deadline)
**Event:** June 24-28, 2026 — Doe Bay Resort, Orcas Island, WA
**Format:** Submit for both a 50-minute talk AND a workshop (workshop = free admission)

---

## SUBMISSION 1: Talk (50 minutes)

### Title

**Frio: Building a Self-Improving Zero-Knowledge Network to Monitor ICE Detention**

Alt titles (pick one):
- "Be Everywhere, Be Nowhere: Zero-Trust Architecture for the Surveilled"
- "The System That Can't See Its Own Data: Privacy-Preserving Detention Monitoring"

### Abstract

When my friend was detained by ICE, I needed to know where he was. Jail rosters are technically public, but they're scattered across hundreds of county systems, many behind CAPTCHAs, and searching them means creating a trail that could be turned against the people you're trying to protect.

So I built Frio — a monitoring service that watches detention facilities without being able to see who it's watching for. The system uses NaCl sealed-box encryption so that search queries and results are only readable by the requesting family, never by the system operator. If Frio is ever subpoenaed, there's nothing to hand over.

But a single scraper on a single machine isn't enough. Facilities deploy CAPTCHAs, rate limit aggressively, and change their platforms without warning. So Frio evolved into a distributed collection network built on Spiritwriter, an open-source agent governance framework I built to solve the hard problems: How do you delegate work to untrusted agents? How do you enforce budgets and capability scopes across a swarm? How do you maintain a cryptographic audit trail when no single node sees the full picture?

In Spiritwriter's model, every job is an encrypted shard with scoped entitlements — capability tokens that grant specific permissions (read this shard, execute this ability, spend up to $5) with expiry and per-shard encryption keys. Agents check their entitlements before acting, and every access is logged to an immutable hash chain. The system doesn't trust its own nodes, and it shouldn't.

Volunteer contributors run a Tampermonkey userscript that receives encrypted job shards via IPFS, lets their real human browser handle the CAPTCHA, and returns encrypted results — without ever seeing who they searched for or what they found. The system is also self-improving: contributors' browsers automatically profile new facility sites, generate scraping abilities, and submit them back to the network where an evaluation agent validates and deploys new capabilities. The system literally researches how to make itself better.

This talk covers:

- **The zero-knowledge model:** NaCl sealed-box dual-key encryption, content-addressed memory shards with decay classes, and why the operator must never be able to read their own database
- **Spiritwriter's governance layer:** capability-scoped entitlement tokens, budget caps, cryptographic trace chains, and how to delegate work to agents you don't trust
- **Reverse-engineering OCV/TheSheriffApp:** the dominant jail roster platform serving hundreds of county sheriffs, and how it works under the hood
- **The adversary contributor problem:** how to build a network where even hostile participants are forced to contribute real value while learning nothing useful. (Spoiler: if they install the extension, they help whether they like it or not.)
- **IPFS + Tailscale split transport:** contributors see only DHT, operators manage via Tailscale mesh, nodes bridge both networks
- **ICE A-number searches and the organic distribution argument:** one browser searching many A-numbers is suspicious; 100 browsers each searching one is indistinguishable from 100 families
- **Percival: camouflage-through-alignment** — public-facing mirror pages that make each node look like it serves a different purpose than it does
- **The self-improving loop:** in-browser site profiling, versioned scraping abilities, evaluation agents, and recursive capability expansion — agents that return not just results but improved abilities for the next agent
- **20 years of distributed surveillance systems:** patterns I first built for piracy content monitoring, now repurposed for human rights
- **Real-world tradeoffs:** legal risk surfaces, CAPTCHA ethics, the tension between zero-knowledge and aggregate intelligence, and what happens when the infrastructure you built to help one person could measure the scope of illegal detention nationally

### Bio

Aaron Markham is a technologist and entrepreneur with two decades of experience in distributed systems, AI/ML, and R&D program leadership. He previously led a multi-million dollar R&D program at GE Global Research / NBCUniversal that produced real-time QB motion tracking for the inaugural 2006 NBC Sunday Night Football season, and has built distributed agent systems for content monitoring, real-time video analysis, and privacy-preserving infrastructure. He's currently building Frio (frio.help) and releasing the Spiritwriter agent governance framework as open source — while on leave from his day job, and trying to get his friend out of ICE detention.

### Technical Level

Advanced. Assumes familiarity with public-key cryptography concepts, distributed systems basics, and a general understanding of web scraping. No specific language or framework knowledge required.

### Slides/Demo

Live demo of the Frio intake flow, showing encrypted query submission and result delivery. Architecture walkthrough with system diagrams. Aggregate data from public case monitoring (detention timelines, facility patterns). If the contributor network is running by June, live stats from the distributed collection fleet.

---

## SUBMISSION 2: Workshop (90-120 minutes)

### Title

**Build a Privacy-Preserving Alert Service: Zero-Knowledge Monitoring in 90 Minutes**

### Abstract

Your government publishes data you need to monitor — but searching for it creates a trail you can't afford. In this workshop, you'll build a minimal zero-knowledge notification service from scratch: a system that watches public data sources for matches against encrypted queries, without the operator ever being able to see what's being searched for or who's asking.

This isn't theoretical. The patterns come from Frio, a real system monitoring ICE detention facilities for families of detainees, built on the Spiritwriter agent governance framework. But the architecture generalizes to any scenario where you need to watch a public data source without revealing your interest: court filings, license plate databases, arrest records, regulatory actions, sanctions lists — any public dataset where the act of searching is itself sensitive.

**What you'll build:**

1. **Keypair generation** — NaCl sealed-box encryption with dual keys (service key for processing, requestor key for results)
2. **Encrypted intake** — a requestor submits a query that the system can process but never read
3. **Fuzzy matching** — search a simulated data source with privacy-preserving name matching and configurable thresholds
4. **Encrypted result delivery** — matches are encrypted to the requestor's key; the operator sees only that a match occurred, not the content
5. **Shard decay** — data doesn't persist indefinitely; implement time-based expiration classes so the system forgets

**What you'll take away:**

- A working prototype you can extend for your own use case
- Understanding of zero-knowledge service architecture
- Practical NaCl/libsodium usage patterns
- A framework for thinking about surveillance-resistant system design
- Exposure to Spiritwriter's shard and entitlement model for building governed agent systems

**Prerequisites:** Laptop with Python 3.9+. We'll provide a starter repo built on spiritwriter-core. Familiarity with Python and basic crypto concepts helpful but not required — we'll explain as we go.

**Materials provided:**

- Starter code repo with scaffolding and test data (built on spiritwriter-core)
- Reference architecture diagram
- Cheat sheet: NaCl sealed-box operations in PyNaCl
- Spiritwriter quick-start guide for extending the prototype into a distributed system

### Bio

(Same as talk submission)

### Technical Level

Intermediate. Python proficiency helpful. Crypto concepts explained from first principles — you don't need to know what a sealed box is coming in.

### Capacity

20-30 participants ideal. Larger groups possible with helper volunteers.

---

## NOTES FOR AARON (not part of submission)

### What changed in this revision
- **Spiritwriter named explicitly** — this is the open-source debut. The talk introduces it, the workshop gets people using it. Two birds.
- **Governance framing added** — the entitlements/capability/trace stack is the differentiator. "How do you delegate work to agents you don't trust?" is the question nobody else is answering with shipping code.
- **Self-improving loop sharpened** — agents return improved *abilities* (not just results). This is the versioned capability evolution we discussed. "Abilities" distinguishes from Claude Code "skills" — abilities are versioned, distributed agent capabilities that travel in shards.
- **Bio updated** — mentions Spiritwriter as open-source release, not just Frio.
- **Workshop ties to spiritwriter-core** — attendees leave with spiritwriter installed and a working prototype. Instant adoption funnel.

### Sprint priorities for CFP strength (by April 1)
1. **Seed Patin Patin case into Frio** — concrete monitoring data makes the CFP real ("we've been tracking public cases since March")
2. **IPFS shard prototype** — "we've demonstrated IPFS-based job distribution" vs. "we plan to"
3. **Tampermonkey skeleton** — even a non-functional scaffold shows the contributor model is real code
4. **Percival page on nl1** — one page of aggregate stats at a URL you can point to
5. **spiritwriter-core README + quick-start** — if a reviewer Googles it, something should exist

### CFP strategy
- Submit for BOTH talk and workshop. Workshop = free ticket. Talk = better visibility.
- First deadline is April 1. Submit even if imperfect — you can update before May 4.
- The personal narrative is the hook. Lead with "my friend was detained" not "here's a cryptographic protocol."
- The self-improving loop is the ToorCamp differentiator. Lots of people build scrapers. Nobody builds scrapers that research how to make themselves better.
- The Patin Patin case (March 16, warrantless raid, judge ordered release) is timely and concrete.
- "20 years of distributed surveillance systems repurposed for human rights" is a strong throughline for the hacker camp audience.
- **NEW: Spiritwriter as open-source launch** — ToorCamp is the venue. Workshop is the adoption funnel. Talk is the story. GitHub repo is the artifact.

### Workshop logistics
- Prep starter repo with scaffolding by mid-June (spiritwriter-core must be pip-installable by then)
- Test the workshop flow with 2-3 friends before ToorCamp
- Consider: can the workshop output actually be used to join the Frio contributor network? Recursive recruitment.
- Need offline fallback — Doe Bay WiFi is notoriously bad. Pre-download all deps.

### Open questions
- How much of spiritwriter-core needs to be public by April 1? (Minimum: README + architecture doc. Code can follow.)
- Should the talk explicitly frame Spiritwriter as "the governance layer the industry doesn't have yet"? Or let the audience draw that conclusion?
- Percival naming — keep it or rename? It's a good name but needs a one-sentence explainer in the talk.

---

*Last updated: March 20, 2026*
