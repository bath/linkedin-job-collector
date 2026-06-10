You are filtering LinkedIn posts for a software engineer's remote job hunt.

From the JSON list of posts below, KEEP only posts that are genuine hiring
signals for remote (or remote-friendly) software engineering roles:

- A company, hiring manager, or recruiter announcing an actual open SWE role.
- Posts that name a role, a team, or a way to apply/DM.

DROP:
- Career coaches, resume services, "comment 'interested' for my course" bait.
- #opentowork posts from job *seekers* (we want posts that are hiring, not looking).
- Generic motivational/engagement-farming content.
- Roles that are clearly on-site only, or not software engineering.
- Recruiter posts with no concrete role, just "DM me for opportunities" spam.

Output TWO things:

1. A fenced JSON block listing the URNs you kept:
```json
{"kept": ["urn:li:activity:...", "..."]}
```

2. After the JSON, a ranked markdown digest of the kept posts (best matches
   first). For each: **company/author** — one-line role summary, then the post
   URL on its own line. Keep it skimmable.

If nothing qualifies, return `{"kept": []}` and a one-line "No matches" note.
