# Staff Domain — Owner-Changed Re-Engagement Sequence
## Build Specification for the Sonnet Generation Pipeline

**Version:** 1.0 — 17 Aug 2026
**Owner:** JP (justinp@staffdomain.com)
**Model:** Claude Sonnet 5 (`claude-sonnet-5`) via the Anthropic API
**Audience for this document:** developers building the generation pipeline. Everything a builder needs is in this file. If the pipeline produces output that a reviewer flags, the fix goes into this spec first and the pipeline second, so this document stays the single source of truth.

---

## 1. What this is

We are re-engaging past prospects: contacts and companies we have had real conversations with, sometimes meetings, proposals, even booked candidate interviews, that never converted into clients. This lane ("Lane A") covers records where the **contact owner has changed** since the last contact, which almost always means the rep they dealt with has left the business. The emails are written as a **handover introduction from the new account manager**.

The pipeline generates **5 emails per contact** and writes them into 10 contact properties in HubSpot (`email_1_subject`, `email_1_body` … `email_5_subject`, `email_5_body`). A HubSpot sequence then sends them on a Day 1 / 5 / 10 / 15 / 21 cadence, pulling each property as a personalisation token. The sequence is **rep-sent** (5 touches exceeds the 3-touch agent limit).

We will point the pipeline at a HubSpot list. Do not hard-code any list ID.

Two things this pipeline is NOT:

- It is not cold outbound. Every record has real history. The emails must read like someone who has read the file, because the generation input includes the file.
- It is not a mail merge. Personalisation must change the *reason* for each email, not decorate it. A record with a dead Estimator deal gets a different email 3 than a record with no deal at all.

---

## 2. Pipeline overview

```
For each contact on the list:
  STAGE 1  Assemble the record          (HubSpot reads, deterministic code)
  STAGE 2  Apply exclusion filters      (deterministic code)
  STAGE 3  Route the vertical           (deterministic code, lookup table)
  STAGE 4  Generate the 5 emails        (one Sonnet call per contact)
  STAGE 5  Lint the output              (deterministic code; regenerate on hard failures)
  STAGE 6  Assemble final bodies        (append link lines, deterministic code)
  STAGE 7  Write back to HubSpot        (batch update the 10 properties)
```

Stages 1–3 and 5–7 are code, not model calls. The model does exactly one job: write five email bodies and subjects from a fully-assembled research brief. Everything the model needs is handed to it; it must never fetch, guess, or infer facts that are not in the brief.

---

## 3. STAGE 1 — Assemble the record

This is the most important stage. The quality of the emails is set here, not in the prompt. Build one research brief per contact with the following components.

### 3.1 Start at the COMPANY, not the contact

Fetch the contact's associated company, then **all contacts associated with that company**. For each associated contact capture: `firstname`, `lastname`, `jobtitle`, `num_contacted_notes`, `notes_last_contacted`.

Why: the email must be able to say "I've been through the notes with yourself, Darren and Gerard". Naming one or two real colleagues is the single strongest proof that the relationship is real. On our data most accounts have 2–8 contacted people; some have 18.

Rules for using colleagues in copy:

- Only name colleagues who have actually been contacted (`num_contacted_notes` >= 1).
- Prefer the 1–2 most-contacted or most senior colleagues. Never list more than two names besides the recipient.
- First names only in copy.
- If the recipient is the ONLY contact on the company, the brief must say so explicitly, and email 1 falls back to the pure relationship handover (no colleague reference). Do not let the model invent a colleague.

### 3.2 Deal history (company level)

Fetch all deals associated with the **company** (not just the contact — deals are frequently associated with a different contact at the same company). Capture: `dealname`, `dealstage`, `createdate`, `closedate`.

- Deal names are the actual roles we quoted ("Estimator", "L2 Helpdesk Engineer", "Cabinet Vision Drafter"). They are the requisition data.
- Discard junk deals: any dealname starting with `(Test)`, `(delete)`, or containing the word `test` as a standalone token.
- Summarise for the brief: role names, when created, when closed, and the stage they died at. Group multi-deal bursts ("four roles scoped together in Feb 2026, all closed lost by June").
- **No-deal branch:** if the company has zero deals, the brief must state "No previous deal on record. Do not invent one." This flips email 1 to relationship-only and email 3 to industry observation instead of role follow-up. Expect this to be common on this list — in sampling, most owner-changed accounts had heavy contact history but no deal ever created.

### 3.3 Notes (the story layer)

Fetch notes associated with the contact and its 1–2 key colleagues. **Filter out bot noise before anything reaches the brief.** Discard any note whose body starts with or contains within the first 80 characters:

- `A new opportunity is available for` (job-ad alert bot)
- `Prospect Smarter`
- `Enrichment Successful`
- `Link to Job Post` / `Job url:` / `job url:` / `Job URL:`
- `Sent pattern interrupt email`

What remains (discovery call notes, SDR qualification forms, meeting summaries, proposal records) is the story: why they were hiring, what they had tried before, budget/urgency, what stopped it. Summarise into 2–5 bullet points for the brief.

The job-ad alert notes are NOT useless — parse them separately into a "live hiring signals" line (role title + month) because "they advertised an Operations/Contracts Administrator three times this year" is a legitimate, publicly-sourced observation. But mark it clearly as *public job-ad data* in the brief, because the copy rules treat it differently (see 7.6).

**Sensitive content rule:** if a note contains anything critical of the prospect's staff, their business, or their internal politics ("CEO is mad", "repeating same errors"), or names a competitor provider, the brief may carry it as context but MUST tag it `INTERNAL - NEVER REFERENCE`. The system prompt reinforces this, but tag it at the data layer too.

### 3.4 Resolve the handover name (the critical step)

The handover name is the first name of the **last person from our side who actually contacted this person**, by call or email, whichever is most recent.

**Do NOT use contact properties for this.** We verified in this portal:

- `last_activity_user` is empty on every contact checked.
- `latest_email_sender_name` is populated on roughly 1 in 5 contacts, and when populated it stores an owner ID, not a name.
- `hubspot_owner_id` is the CURRENT owner, which on this list is by definition NOT the person they last dealt with.

**Correct method:** fetch the engagement objects.

1. Fetch all CALL engagements associated with the contact, take the most recent by `hs_timestamp`, note its `hubspot_owner_id`.
2. Fetch all EMAIL engagements associated with the contact (outbound only, `hs_email_direction` = `EMAIL`), take the most recent, note its `hubspot_owner_id`.
3. The later of the two is the **last-activity owner**. Resolve the owner ID to a name via the owners API, and note whether that owner `isActive`.

### 3.5 Exclusion filters (STAGE 2)

Apply before generation. Records that fail go to a report, not the bin — JP reviews the exclusions.

| # | Filter | Why |
|---|--------|-----|
| E1 | **Last-activity owner == current owner.** | The "new" owner is already actively working this record. A handover email would be false and would collide with live outreach. Verified example: a record whose owner "changed" but whose current owner had called them the same day and emailed them three days prior. These records belong in Lane B or a live-outreach bucket. Expect a meaningful slice, not a handful. |
| E2 | **Last-activity owner is still active** (but is not the current owner). | "I've taken over from [name]" implies they left. If they are still here it reads as false to anyone who checks. Route to a same-owner-adjacent variant or hold for JP. |
| E3 | **Any activity in the last 14 days.** | Something is already in flight. Hold. |
| E4 | **Duplicate contact records** (same person, same company, two record IDs). | They would receive the sequence twice. Report for merge. We found live examples in sampling. |
| E5 | **No engagement history at all** (zero calls and zero outbound emails). | There is no relationship to hand over. These are cold records mis-filed on a re-engagement list. |
| E6 | **Unsubscribed / bounced / invalid email.** | Standard hygiene. |

If the record passes: the handover first name goes in the brief, e.g. "The last person to contact them was Sabrina Solibaga (call, 29 Apr 2026). Sabrina has left the business. Open email 1 by saying you have recently taken over the account from Sabrina."

### 3.6 Geography check

Check the company's country and phone prefix. If the record is not Australian (+44, +1, +65 etc.):

- The brief must state the country and instruct: "Nothing in the copy may assume Australia."
- Keep the copy country-neutral: no Australian market references, no assumed geography. The playbook's "assumed Australian context" rule applies only to AU records (for those, never write "Australian [thing]" — the context is assumed, not stated).

### 3.7 The assembled brief (input contract to the model)

One plain-text block per contact, built from the above. Field order matters (the model reads top-down):

```
CONTACT: {firstname} {lastname}
JOB TITLE: {jobtitle or "not recorded"}
COMPANY: {company name}
INDUSTRY: {Industry (Other) property, or "not recorded"}
COUNTRY: {country; state "Australia" explicitly or name the other country}

HANDOVER: The last person to contact them was {First name} ({call|email}, {date}).
{First name} has left the business. Open email 1 by saying you have recently
taken over the account from {First name}.

COLLEAGUES WE ALSO DEALT WITH: {First name} ({job title}), {First name} ({job title})
  — name one or two of them by first name in email 1.
  [or] The recipient is the only contact on this account. Do not reference colleagues.

DEAL HISTORY: {summary with role names and dates}
  [or] No previous deal on record. Do not invent one. Email 1 is relationship-only;
  email 3 leads with an industry observation, not a role follow-up.

WHAT THE NOTES SAY: {2–5 bullets of real story}
INTERNAL - NEVER REFERENCE: {sensitive items, competitor names, critical remarks}

LIVE HIRING SIGNALS (public job ads): {role titles + months, if any}

CASE STUDY FOR EMAIL 2: {name from routing table}. Do not describe its contents.
EMAIL 3 INLINE PAGE: {url from routing table}
EMAIL 4 INLINE PAGE: {url from routing table}
EMAIL 1 CLOSE: Use close option {n} from the close bank.
```

The **close option** is assigned by code, rotating through the bank (section 8.2) with these overrides: option 3 for heavily-contacted records (>= 30 touches), option 4 for C-suite titles (CEO/MD/COO/CFO/Partner/Principal/Director where it is clearly top-of-business), option 5 where the case study match is strong (exact industry match in the routing table). Otherwise rotate 1 → 2 → 1 → 2.

---

## 4. STAGE 3 — Vertical routing table

Route on the contact `industry` property ("Industry (Other)", 95% filled, LinkedIn-style values). Substring match, case-insensitive, first hit wins, top-to-bottom:

| Match tokens (any) | Case study for email 2 placeholder | Email 3 inline page | Email 4 inline page |
|---|---|---|---|
| accounting, bookkeep, tax | SJM Accountants (accounting firm) | https://www.staffdomain.com/accounting-finance/ | https://www.staffdomain.com/build-your-team/ |
| staffing, recruit, human resources, executive search | Cox Purtell (recruitment) | https://www.staffdomain.com/solutions/recruitment/ | https://www.staffdomain.com/build-your-team/hr-recruitment/ |
| it services, information technology, computer software, software, computer network, cyber, telecommunications, technology | Systemnet (Sydney MSP) | https://www.staffdomain.com/technology/ | https://www.staffdomain.com/build-your-team/ |
| construction, civil engineering, building, architecture, engineering, joinery, design | Carrera by Design (construction/joinery) | https://www.staffdomain.com/construction-engineering/ | https://www.staffdomain.com/build-your-team/construction-support/ |
| hospital, health, medical, care, wellness | Verus (healthcare) | https://www.staffdomain.com/health-care/ | https://www.staffdomain.com/build-your-team/ |
| law, legal | Elias Gates (legal) | https://www.staffdomain.com/professional-services/ | https://www.staffdomain.com/build-your-team/ |
| marketing, advertising, public relations, graphic design, media | Capital-E (marketing and events) | https://www.staffdomain.com/professional-services/ | https://www.staffdomain.com/build-your-team/sales-marketing/ |
| real estate, property, leasing | Capital-E (finance team) | https://www.staffdomain.com/real-estate/ | https://www.staffdomain.com/build-your-team/ |
| financial services, insurance, investment, banking, capital markets | Durst Industries (accounting) | https://www.staffdomain.com/accounting-finance/ | https://www.staffdomain.com/solutions/ |
| consulting, professional training, business services | Interlinked (professional services) | https://www.staffdomain.com/professional-services/ | https://www.staffdomain.com/solutions/ |
| logistics, transportation, supply chain, warehousing, maritime | Liftango (logistics) | https://www.staffdomain.com/industries/ | https://www.staffdomain.com/build-your-team/ |
| retail, e-commerce, consumer, hospitality, food, leisure | EatFirst (customer service) | https://www.staffdomain.com/build-your-team/customer-service/ | https://www.staffdomain.com/build-your-team/ |
| manufactur, machinery, mining, industrial, wholesale, automotive, utilities, oil and gas, chemical, printing | Bells Pure Ice (manufacturing) | https://www.staffdomain.com/industries/ | https://www.staffdomain.com/build-your-team/ |
| *(no match, or industry empty)* | Bells Pure Ice (manufacturing) | https://www.staffdomain.com/industries/ | https://www.staffdomain.com/build-your-team/ |

**Hard URL rules:**

- The URLs above are the COMPLETE whitelist. The model must never emit any other URL, and the linter enforces it. All are verified against the current sitemap.
- Never link a URL whose slug contains `offshore`, `outsourcing`, or `bpo` (e.g. `/solutions/offshore-staffing/` exists on the site — it is banned here because the slug leaks vocabulary the copy deliberately holds back).
- Never link an individual case study URL. Case studies are always the named placeholder (section 6).
- Thin case studies (SJM, Verus, Interlinked, Liftango, Money Metrics, Genesis, Cox-adjacent thin ones): the brief carries no detail about them and email 2 must stay to a single relevance line. This is enforced by the "never describe the case study" rule either way.

---

## 5. STAGE 4 — Generation call

### 5.1 Model configuration

| Setting | Value |
|---|---|
| Model | `claude-sonnet-5` |
| Max tokens | 2000 |
| System prompt | Section 5.2, verbatim, with `cache_control: {type: "ephemeral"}` so it is written to the prompt cache once and read at 10% price for every subsequent contact |
| User message | The assembled brief from 3.7 |
| API mode | Message Batches API if run as one job (50% discount; at ~500 contacts the whole run costs a few dollars either way). Realtime is fine for iteration. |
| One call = one contact = all five emails | Do not generate emails individually; the model needs to see the whole arc to avoid repeating itself across touches. |

### 5.2 The system prompt (lift verbatim)

Everything between the fences is the system prompt. Do not paraphrase it, do not "improve" it, and keep it under version control — every voice failure we found in testing traces to a rule below.

```
You write re-engagement emails for Staff Domain, an Australian company that builds
dedicated teams for businesses, with people who work exclusively for one client as
an extension of their team.

THE SITUATION. The recipient is a past prospect. We had real conversations with
them, sometimes proposals and interviews, and it never converted. The rep they
dealt with has since left the business. You are writing as the NEW account manager
introducing yourself and gently re-opening the relationship. This is a warm
handover, not cold outbound. Everything you may reference is in the brief you are
given. The brief is the entire universe of facts.

VOICE. This is the most important instruction and the easiest to get wrong.

The register is a capable Australian salesperson writing a quick personal email to
a business owner they have context on. Relaxed, human, unfussy. It is NOT a mate
at the pub, and it is NOT a marketing department. These readers are directors and
managers. They will forgive informality. They will not forgive sounding like a
caricature.

THE CORE RULE: casualness comes from SENTENCE CONSTRUCTION, never from slang.
Keep the sentences loose and the vocabulary plain. The moment you reach for a
colloquialism to sound Australian, you have gone too far.

Get the casual feel from these:
- Contractions everywhere: I've, didn't, it's, that's, haven't, wouldn't, you've.
- Short sentences, and the occasional fragment. "Quick one." "Genuine question."
- Loose, slightly run-on construction is correct. Do not tidy every sentence into
  perfect grammar.
- Soft hedges: sort of, roughly, from memory, I'd imagine, (ish).
- Direct address: your setup, at your end, you guys.
- Low-stakes closers: have a look when you get a minute, it's a good read.
- Mix "I" for your own actions with "we" for the company. Both are fine.

Take the vocabulary from plain business English:
business, tried, didn't work out, capacity, the support side, coverage,
resourcing, cost, hard to find, went ahead, changed, straightforward, worth a
look, in place, eased up.

NEVER use these words or phrases. They read as caricature:
mob, have a crack, been burnt, no dramas, no worries, all good, reckon, up your
alley, leave you be, brutal, silly money, chew up, grief, the lot, got up
(meaning proceeded), upstairs (meaning management), keen, mate, heaps, bloke,
arvo, spot on, sorted, flat out, stuck into, fair dinkum, chuck, gonna, yeah nah,
squiz.

Use at most ONCE across the whole five-email sequence or they become a tic:
a fair few, playing catchup, quick one, worth a read, fair bit.

CALIBRATION. The same line at three registers. Always write the middle one.
  TOO CASUAL: "Construction mob, similar sort of size, who'd had a crack at this
              and been burnt."
  CORRECT:    "Construction business, similar size to yours, who'd tried this once
              before and had it not work out."
  TOO FORMAL: "The case study concerns a construction organisation of comparable
              scale whose initial engagement proved unsuccessful."

  TOO CASUAL: "The support market hasn't got any kinder this year."
  CORRECT:    "The support market hasn't eased up this year."
  TOO FORMAL: "Conditions in the support talent market remain challenging."

  TOO CASUAL: "If not, no dramas at all, I'll leave you be."
  CORRECT:    "If not, that's completely fine. I'll leave it with you."
  TOO FORMAL: "Should this not align with your current priorities, I quite
              understand."

  TOO CASUAL: "Did the build ever get up, or did it get parked when things
              shifted upstairs?"
  CORRECT:    "Did the build ever go ahead, or did it get parked when the
              leadership changed?"
  TOO FORMAL: "Was the proposed technical expansion ultimately implemented?"

HARD MECHANICAL RULES.
- No em dashes anywhere. Use a comma or a full stop.
- Subject lines: lowercase, 2 to 7 words, under 45 characters, never hinting at a
  pitch, never starting with "just".
- Never use: just checking in, I never heard back, hope this finds you well, grab
  a time, walk you through, no obligation, tailored plan, reach out, circle back,
  good to reconnect, worth exploring, no sales pitch, touching base.
- Never write "no agenda, no pitch" or any stacked double disclaimer. The email
  earns its no-pressure feel from the close, not from declaring what it isn't.
- Never say offshoring, outsourcing, BPO, offshore, onshore or nearshore in the
  copy. Say "dedicated team", "someone who works only for you", "a person in our
  office on your hours". (The word may appear in the brief; it must not appear in
  the email.)
- Never invent a name, role, number, date, colleague or event. Use only what the
  brief gives you. If a field is missing, write around it.
- Never quote or paraphrase anything the brief marks INTERNAL - NEVER REFERENCE.
  Never repeat critical remarks about their staff or business. Never name another
  provider they may have used.
- Job-ad signals in the brief are public information and may be referenced, but
  vaguely ("I noticed you were out in the market for desktop support") and never
  with the ad's URL, salary, or exact posting details, or it feels surveilled.
- Australian English spelling. For Australian records the Australian context is
  assumed: never write "Australian businesses like yours" or "Aussie". For
  non-Australian records (the brief will say), nothing may assume Australia.
- Each email is 45 to 110 words. Shorter is better.
- Every email ends with [Rep first name] on its own line, nothing after it.
  No "Kind regards", no "Cheers", no signature block.
- Numbers we are allowed to state: roles are usually scoped in one call;
  candidates typically in front of them inside one to two weeks; a seat generally
  live within about four weeks; the person works only for them, from our office,
  on their hours. State nothing else as fact: no savings percentages, no prices,
  no client counts.

THE FIVE EMAILS.

EMAIL 1, Day 1. The handover introduction. Exactly four content parts:
 (a) Greeting: "Hi {first name},"
 (b) The handover, one short line naming the previous rep from the brief:
     "I've recently taken over your account from {name}." Vary the verb
     (taken over / picked up) but keep it one line.
 (c) The proof-of-homework line: one sentence showing you have read the file.
     Reference what the brief supports: colleagues by first name, the old role,
     the rough timing. If the brief says no deal, reference the relationship
     ("we've been in touch a fair few times over the years without it ever
     turning into a proper conversation"). Never more than two colleague names.
 (d) The close: use the EXACT close from the close bank option named in the
     brief, word for word. Do not improvise a close.
 NO link. NO ask. NO pitch. Nothing about what Staff Domain does. If email 1
 explains the service, it has failed.

EMAIL 2, Day 5. The case study pointer. You are given the case study NAME only.
 Do NOT describe, summarise, or hint at its contents, not even one detail. Say
 you were reading a case study of a client in a similar situation and thought it
 worth passing on, with ONE light line about why it is relevant to them (their
 industry or their old need, drawn from the brief). Then invite them to have a
 look when they get a minute. The link itself is appended after generation; do
 not write any URL or placeholder.

EMAIL 3, Day 10. Their world. One useful, specific observation about hiring or
 capacity in THEIR situation. Priority order for the angle:
   1. The old deal role, if one exists ("did the estimator search land?")
   2. A live hiring signal from the brief, referenced vaguely
   3. An industry-level observation tied to their vertical
 Weave the EMAIL 3 INLINE PAGE url from the brief into a sentence mid-email with
 a natural lead-in, e.g. "There's a rundown of the construction support roles at
 {url} ." The url must appear inside the body text with a reason attached, never
 dumped on its own line. End with one genuine question to them, as its own
 paragraph. No meeting ask.

EMAIL 4, Day 15. What has changed. The bridge is that things have moved since
 they last looked: roles scoped in one call, someone typically starting within
 about four weeks, the person works only for them from our office on their
 hours. If the brief includes a specific stall reason (price, a previous bad
 experience, a process that stopped), address it head-on in one honest line
 without quoting the record back at them. Weave the EMAIL 4 INLINE PAGE url into
 a sentence the same way as email 3. Soft offer to show them, no booking link.

EMAIL 5, Day 21. The easy ask and the step-back, in one email. One clear,
 low-friction ask: 15 minutes on a call, and say you'll come prepared (profiles,
 something concrete) rather than pitching. Then bow out gracefully: the focus is
 on YOU stepping back, never on their silence. No re-pitch, no benefits list, no
 guilt. The booking link is appended after generation; write a natural lead-in
 that expects a link to follow ("Link's below", "the link below gets 15 minutes
 in the diary"). Do not write the URL or placeholder yourself.

VARIETY ACROSS THE SEQUENCE. Read your own five emails before finishing. Openers
must not repeat. No phrase of four or more words may appear in two emails. The
question in email 3 and the ask in email 5 must be phrased differently. If two
contacts at the same company are on this list they may compare emails, so lean
on the brief's specifics, not on stock sentences.

OUTPUT. Return ONLY valid JSON, no preamble, no markdown fences:
{"e1":{"subject":"...","body":"..."},
 "e2":{"subject":"...","body":"..."},
 "e3":{"subject":"...","body":"..."},
 "e4":{"subject":"...","body":"..."},
 "e5":{"subject":"...","body":"..."}}
Body text uses \n\n between paragraphs. The greeting and the [Rep first name]
sign-off are part of the body. Do not include any URL except the single inline
url in e3 and the single inline url in e4, exactly as given in the brief.
```

### 5.3 The close bank (referenced by the system prompt)

Locked wording — these are JP's own lines. The brief names one option per contact; the model must use it verbatim as email 1's final content line before the sign-off.

| # | Close (exact text) | Assignment rule (code-side) |
|---|---|---|
| 1 (anchor) | Just getting across it all at the moment, and wanted to introduce myself. I'm sure our paths will cross at some stage. | Default rotation |
| 2 | Still working my way through the history at the moment, so this is a hello more than anything. No doubt we'll speak properly down the track. | Default rotation |
| 3 | Mostly wanted to make sure the handover didn't happen quietly without you knowing. I expect we'll cross paths before long. | Heavily-contacted records (>= 30 touches) |
| 4 | Nothing more to this than putting a name to the new face on your account. We'll catch up properly at some stage down the track, I'm sure. | C-suite / principal recipients |
| 5 | Getting my bearings on everything at the moment more than anything else. Once I'm across it all properly we can have more of a chat when the time suits. | Strong case-study match (exact vertical hit) |

Note: if the assigned close's wording collides with the proof-of-homework line (e.g. both say "history"), the model adjusts the homework line, never the close.

---

## 6. STAGE 6 — Assemble final bodies (links)

Link placement is deterministic code, applied AFTER generation. The model writes no links except the two inline URLs it is explicitly given.

| Email | Link treatment |
|---|---|
| 1 | None. The linter rejects any URL in email 1. |
| 2 | Append on a new line at the VERY BOTTOM of the body, after the sign-off: `[Insert {case study name} case study link here]` — the named placeholder tells the rep/DevOps which case study URL to hard-code. |
| 3 | Inline URL only (already in the body from generation). Nothing appended. |
| 4 | Inline URL only. Nothing appended. |
| 5 | Append at the very bottom, after the sign-off: `[Insert rep booking link here]` — the rep's own HubSpot meeting link is hard-coded per sender. |

Final email 2 body shape, top to bottom: greeting → 2–3 short paragraphs → sign-off → blank line → case study placeholder line.

---

## 7. STAGE 5 — Lint (run on every output; hard failures regenerate once, then flag)

### 7.1 Hard failures (regenerate)

1. Output is not valid JSON with keys e1–e5, each with `subject` and `body`.
2. Any em dash (`—`) anywhere.
3. Any banned word/phrase (the caricature list AND the banned-phrase list from 5.2), case-insensitive, in any body or subject.
4. Any occurrence of `offshore`, `offshoring`, `outsourc`, `bpo`, `onshore`, `nearshore` in body or subject.
5. Subject: uppercase letters, > 45 chars, < 2 or > 7 words, or starts with "just".
6. Any URL not on the whitelist (the two URLs given in the brief are the only ones allowed; email 1, 2, 5 must contain zero URLs).
7. Body not ending with `[Rep first name]` as the last line (before code-appended placeholder lines).
8. Any body < 40 or > 120 words.
9. The assigned close (5.3) missing from email 1 or altered by more than punctuation.
10. Names present in the copy that are not in the brief (check every capitalised first name in the output against brief names + recipient + previous rep). This is the invention detector.
11. `no agenda` or `no pitch` appearing anywhere.
12. Content marked `INTERNAL - NEVER REFERENCE` appearing in any form (token-overlap check against those bullets).

### 7.2 Soft warnings (flag for human review, do not regenerate)

- A tic-capped phrase ("a fair few", "quick one", "worth a read", "playing catchup", "fair bit") appearing more than once across the five emails.
- Any 4+ word phrase repeated across two emails in the sequence.
- Email 3 missing a question mark.
- The inline URL sitting at the start or very end of email 3/4 body (should be mid-body with a lead-in).
- Two contacts at the same company drawing identical subjects (cross-contact check).

### 7.3 Human sample

Before HubSpot write-back, dump every Nth output (N=10) plus ALL soft-warning outputs to a review file for JP. Nothing on this list is high enough volume to justify skipping eyes-on sampling.

---

## 8. STAGE 7 — HubSpot write-back

- Target properties: `email_1_subject`, `email_1_body`, `email_2_subject`, `email_2_body`, `email_3_subject`, `email_3_body`, `email_4_subject`, `email_4_body`, `email_5_subject`, `email_5_body`.
- **The body properties must be MULTI-LINE TEXT.** Single-line text strips the paragraph breaks and the email arrives as one block. Verify property type before the first write; do not discover this on record 400.
- Match/update by Record ID (`hs_object_id`). Batch update API, 100 per batch.
- Paragraph separator: real newlines (`\n\n`). Confirm on 2–3 records in the HubSpot UI that the sequence editor renders the breaks before running the full list.
- The sequence template per step is just the matching token + the rep's signature. The bodies are complete emails including the greeting and `[Rep first name]`; decide with JP whether the sequence signature replaces `[Rep first name]` or the write-back substitutes the sender's real first name — pick ONE, or every email signs off twice.
- The two placeholder lines (`[Insert … case study link here]`, `[Insert rep booking link here]`) are replaced with real URLs at sequence-template level or via a find-replace step before send. **Nothing containing a literal `[` placeholder may ever reach a prospect.** Add a final pre-send check for `[` in any email property on enrolled contacts.

---

## 9. Edge-case matrix (quick reference)

| Situation | Behaviour |
|---|---|
| No deal on company | Relationship-led email 1; email 3 industry observation; brief says so explicitly |
| Recipient is only contact | No colleague names; pure handover |
| Previous rep name unresolvable (no engagements) | Excluded (E5) |
| Last-activity owner == current owner | Excluded (E1) — belongs in Lane B / live bucket |
| Last-activity owner still active but not current owner | Held for JP (E2) |
| Non-AU record | Country-neutral copy; brief flags it |
| Prospect already using another provider (from notes) | Context only; never name the provider; email 4 may address "the comparison" honestly if notes show price was the blocker |
| Prior bad experience logged in notes | Email 4 may acknowledge "if a previous go at this didn't work out" WITHOUT quoting the record; never attribute it |
| Deal died at proposal/agreement/interview stage | Email 4 may say plainly "we got a fair way down the track before it went quiet" — honesty about how far it got is a strength |
| Their core need is something we can't service (cleared/defence work, ultra-specialist engineering) | The email SAYS SO and pivots to the adjacent layer. Never overreach. |
| Recipient's industry already lives in offshoring vocabulary (they run an offshore team elsewhere, per notes) | This is a switch-play record and does NOT belong in this sequence's vocabulary rules — flag to JP rather than generate |
| Multiple contacts from same company on the list | Generate for each, run the cross-contact variety check, and flag pairs to JP (may want only one enrolled) |

---

## 10. Gold-standard output

This is the approved reference sequence (record: senior partner at a Canberra ICT consultancy; deal "Agile Compliance Coach" closed lost Jun 2026; handover from Sabrina; colleagues known; close option 1; routing row: IT). New outputs should read like this.

**e1 · subject:** `new name on your account`

> Hi Graeme,
>
> I've recently taken over your account from Sabrina.
>
> I've been back through the notes from earlier this year, including the compliance coach role and the conversation around your new CEO coming in and building the team out.
>
> Just getting across it all at the moment, and wanted to introduce myself. I'm sure our paths will cross at some stage.
>
> [Rep first name]

**e2 · subject:** `worth five minutes`

> Hi Graeme,
>
> Came across one of our case studies this morning and thought it might be worth sending your way.
>
> IT services business with much the same problem you described, growth arriving faster than they could staff for it.
>
> Have a read when you get a minute.
>
> [Rep first name]
>
> [Insert Systemnet (Sydney MSP) case study link here]

**e3 · subject:** `the cleared work and the rest`

> Hi Graeme,
>
> Something worth saying plainly. The cleared Canberra work isn't something we can help with, and I wouldn't pretend otherwise.
>
> Where we do fit is everything sitting around it. Coordination, documentation, reporting, the delivery admin that quietly eats consultant hours. There's a breakdown of those roles at https://www.staffdomain.com/professional-services/ if it's useful.
>
> Is that layer holding up alright at the moment?
>
> [Rep first name]

**e4 · subject:** `since the compliance coach`

> Hi Graeme,
>
> The process has tightened up a fair bit since we last spoke. One call to scope it, candidates in front of you inside a couple of weeks, and generally someone starting within about four weeks.
>
> If you wanted to see how a team gets put together role by role, it's set out at https://www.staffdomain.com/build-your-team/ .
>
> Happy to talk through what it would look like for the delivery side specifically.
>
> [Rep first name]

**e5 · subject:** `last one from me`

> Hi Graeme,
>
> That's me done for now.
>
> If the delivery support side is worth a look, 15 minutes and I'll come with something specific rather than a pitch. Link's below.
>
> If the timing isn't right, that's completely fine. I'll leave it with you.
>
> [Rep first name]
>
> [Insert rep booking link here]

---

## 11. Launch checklist

1. [ ] Body properties confirmed as multi-line text in HubSpot
2. [ ] Exclusion report (E1–E6) reviewed by JP before generation
3. [ ] 20-record pilot generated, linted, and reviewed by JP eyes-on
4. [ ] Paragraph breaks verified rendering in the sequence editor on 3 live records
5. [ ] Placeholder-bracket pre-send check wired in (`[` must never reach a prospect)
6. [ ] Sign-off substitution decided (token vs write-back substitution) — one, not both
7. [ ] Case study URLs and per-rep booking links hard-coded at template level
8. [ ] Duplicate-contact merges done (report from E4)
9. [ ] Send throttling agreed (dormant list, watch bounces and domain reputation)
10. [ ] This spec version-tagged; any copy rule change lands here first

---

*Companion documents: the Staff Domain Sequence & Cadence Design Playbook (campaign architecture) and the Breeze prompt playbook (Breeze-specific mechanics — NOT used by this pipeline; this pipeline writes finished emails, not Breeze prompts).*
