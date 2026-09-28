# Experiment memo: <experiment name>

Write sections 1 to 3 before the experiment starts, and don't change them afterwards. The dashboard freezes the same protocol when you press Start. Write sections 4 to 6 when you read the results and decide. A filled-in example: [experiment-memo-example.md](experiment-memo-example.md).

| | |
|---|---|
| Experiment key | `<key>` (dashboard: `/experiments/<key>`) |
| Owner | |
| Started / stopped | |
| Status | draft / running / stopped |

## 1. Hypothesis

> If we [change], then [metric] will [increase / decrease] because [reason].

Background: the evidence behind the hypothesis (research, data, earlier experiments), and why it's worth testing now.

## 2. Design

- **Variants and weights:** one control, and each treatment with its share of users.
- **Traffic:** the percentage of users in the experiment, and who is eligible.
- **Primary metric:** the metric that decides. Give its event, its kind (conversion or mean), the direction that counts as better, and its window (how long after a user's first exposure their events count).
- **Secondary and guardrail metrics:** each with its expected baseline. Guardrails are metrics that must not get worse, such as refunds, errors, or unsubscribes.
- **Analysis:**
  - *Sequential (always valid):* you may look at any time and stop as soon as it's significant.
  - *Fixed horizon:* read once, at the planned sample size; reading earlier inflates false positives.
  - Give alpha too (0.05 means a 95% confidence interval).
- **Smallest lift worth detecting:** the relative change below which shipping wouldn't be worth it, and why.
- **Decision rules, fixed now:**
  - significant win on the primary metric: …
  - significant loss: …
  - not significant at the planned sample size: …
  - a guardrail gets significantly worse: …
  - sample ratio mismatch flagged: stop reading the results and find the logging bug.

## 3. Sample size and duration

- **Users per variant:** from the dashboard's estimate, or `GET /admin/sample-size`, with the baseline, the smallest lift, alpha, and power (usually 80%). The estimate is for a fixed-horizon test of a conversion metric.
- **A sequential analysis needs more users** for the same power. The Monte Carlo validation measured 1.75 to 1.88 times the fixed-horizon size ([results/summary.md](results/summary.md), section 4).
- **Duration:** users needed ÷ eligible users per day. Round up to whole weeks, so every day of the week is represented.

## 4. Results

Read on: <date>, from the snapshot computed at <time>.

| Variant | Users | Converted (or mean) | Rate |
|---|---:|---:|---:|
| | | | |

- **Health checks:**
  - the sample ratio mismatch p-value, and whether it was flagged;
  - users who saw more than one variant (excluded from the analysis);
  - exposures that disagreed with the server's own assignment.
- **Primary metric:**
  - the lift, absolute and relative, with its 95% CI;
  - the p-value (always-valid, for a sequential analysis);
  - the verdict, and the dashboard's sentence.
- **Secondary and guardrail metrics:** the same numbers. They are context, not the decision: with many metrics, some will look significant by chance.

## 5. Decision

- What you decided (ship, don't ship, iterate, or keep running), and which rule from section 2 it follows.
- What you expect in production. Use the CI, not only the point estimate.

## 6. Learnings

- What surprised you.
- What you would design differently next time.
- Follow-up experiments.
