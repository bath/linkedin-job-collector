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

For each kept post, also act like an applicant prep agent. Use available
web/company research tools if your harness provides them. If you cannot verify a
fact, either omit it or mark it as inferred from the post. Do not fabricate
company facts.

Output TWO things:

1. A fenced JSON block listing the posts you kept. Each kept item must include:
   - `urn`: the post URN
   - `hook`: one short application/outreach angle for this role
   - `facts`: 5-10 concise facts an applicant should know before applying
```json
{
  "kept": [
    {
      "urn": "urn:li:activity:...",
      "hook": "Lead with your healthcare data-platform experience and ask about the team's Airflow/dbt roadmap.",
      "facts": [
        "Remote-first U.S. role.",
        "Core stack named in the post: Python, SQL, Airflow, dbt, and AWS.",
        "Healthcare data experience is a stated bonus."
      ]
    }
  ]
}
```

2. After the JSON, a ranked markdown digest of the kept posts (best matches
   first). For each, use this bare-bones shape:
   - `**company/author** — role summary`
   - `Hook: ...`
   - `Facts:` with 5-10 bullets
   - source URL on its own line

If nothing qualifies, return `{"kept": []}` and a one-line "No matches" note.
