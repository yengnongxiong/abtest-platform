# Experiment memo: Bigger checkout button

> **Simulated. No real users took part.** This memo follows the [template](experiment-memo-template.md) for the `checkout_button` scenario ([scenarios/checkout_button.yaml](../scenarios/checkout_button.yaml)). The traffic generator sent 40,000 simulated users through the real API, and the platform analyzed them as it would real ones. Because it's a simulation, the true effect is known: the big button raises the purchase rate from 10% to 10.8%, a **+8%** relative lift. Every number below was printed by the command next to it, on 2026-09-28.

| | |
|---|---|
| Experiment key | `checkout-button` |
| Owner | Yengnong Xiong |
| Started / read | Started 2026-09-28 20:56:03 UTC. Results read from the snapshot computed at 20:56:24 UTC (the simulation sends 40,000 users in 21 seconds) |
| Status | Running when read. The decision (section 5) is to stop it and ship |

**Reproduce.** On a fresh database (the key `checkout-button` must be unused), run `make dev`, then `make traffic SCENARIO=checkout_button`. The generator is seeded, and names its users after the experiment key, so the counts in section 4 repeat exactly; only the timestamps change. This was checked by running it twice, on two fresh databases.

## 1. Hypothesis

> If we make the Buy button bigger, purchases will increase because the button is easier to find.

Background: none, since the scenario is simulated. A real memo would cite the evidence here, such as session recordings of users missing the button.

## 2. Design

- **Variants and weights:** Control (the control) 50%, Big button 50%.
- **Traffic:** 100% of users.
- **Primary metric:** Purchase: a conversion metric on the `purchase` event, where an increase is better, with a 7-day window.
- **Secondary and guardrail metrics:** none registered (see section 6).
- **Analysis:** sequential (mSPRT, always valid), alpha 0.05. The expected baseline of 10% and the smallest lift of 8% set the test's mixing parameter to τ = 0.10 × 0.08 = 0.008, which the snapshot records.
- **Smallest lift worth detecting:** +8% relative (10% → 10.8%).
- **Decision rules:**
  - significant win on Purchase: stop and ship the big button;
  - significant loss: stop and keep the current button;
  - not significant by the planned sample size (section 3): stop and keep the current button;
  - sample ratio mismatch flagged: stop reading the results and find the logging bug.

## 3. Sample size and duration

- **Fixed horizon:** 22,855 users per variant, for 80% power to detect +8% from a 10% baseline at alpha 0.05 (`GET /admin/sample-size?baseline=0.10&mde_relative=0.08&alpha=0.05&power=0.8`, the same estimate the dashboard's form shows).
- **Sequential:** the validation measured that the mSPRT needs 1.75 to 1.88 times the fixed-horizon size for 80% power, at lifts of +2%, +5%, and +10% ([results/summary.md](results/summary.md), section 4). That puts this test at roughly 40,000 to 43,000 users per variant (22,855 × 1.75 and × 1.88; +8% itself wasn't simulated).
- **The scenario sends 40,000 users in all, about 20,000 per variant.** That is fewer than even the fixed-horizon plan, and about half the sequential one. The same validation found that at the fixed-horizon size, the mSPRT detected a real lift in only 45.7% to 47.6% of experiments. At this smaller size, this test should reach significance less than half the time, even though the effect is real.

## 4. Results

Read from the snapshot computed at 2026-09-28 20:56:24 UTC, covering all 40,000 users (`make traffic SCENARIO=checkout_button`, which prints them; `GET /admin/experiments/checkout-button/results` returns them in full).

| Variant | Users | Purchased | Rate |
|---|---:|---:|---:|
| Control | 20,062 | 1,927 | 9.61% |
| Big button | 19,938 | 2,176 | 10.91% |

- **Health checks:**
  - Sample ratio mismatch: p = 0.535, not flagged (20,062 vs 19,938 on a 50/50 split).
  - No users saw both variants, and no exposure disagreed with the server's own assignment.
- **Primary metric:**
  - **Lift: +1.31 percentage points** (always-valid 95% CI +0.39 to +2.23), or **+13.6% relative** (+4.0% to +23.2%). The relative interval is the absolute one divided by the control's rate, which is an approximation.
  - Always-valid p-value: 0.0008.
  - Verdict: **significant win**.
  - The dashboard's sentence: "Big button increased Purchase by 13.6% (95% always-valid CI +4.0% to +23.2%). This is statistically significant."
- **The truth**, known only because this is a simulation: +8%. That is inside the interval.

## 5. Decision

- **Ship the big button.**
  - The primary metric shows a significant win, and the sample ratio check is healthy.
  - The analysis is sequential, so stopping at the first significant look doesn't inflate false positives, even this far short of the planned size. That is the property the Monte Carlo validation checks: 0.90% false positives across 20 looks, against 24.62% for a z-test read the same way.
  - Next: stop the experiment with a reason, and roll the big button out to 100% of users.
- **Expected impact:** somewhere between +4% and +23% more purchases, according to the interval. Plan on the low end, not on +13.6% (see the first learning).

## 6. Learnings

- **The winning estimate overstates the effect.** The true lift is +8%, and this run measured +13.6%. The same scenario under two other experiment keys (so other users, and other random draws) measured:
  - +8.4% (CI −1.0% to +17.8%), not significant yet: `make traffic SCENARIO=checkout_button ARGS="--experiment-key checkout-button-2"`;
  - +10.2% (CI +0.7% to +19.6%), significant: the same command with `checkout-button-3`.

  All three intervals contain +8%. But both runs that reached significance overstated the lift, and the run that measured it most accurately wasn't significant. When a test has too few users for the effect it's looking for (section 3), only the draws that happen to come out high cross the significance line. This is called the *winner's curse*. Forecast from the interval's low end, and give the test enough users that a significant result doesn't depend on a lucky draw.
- **Size the test before starting.** 40,000 users was below the fixed-horizon plan (45,710) and about half the sequential one. The sample-size section should set how long the test runs.
- **Add a guardrail.** A bigger button could raise purchases and also accidental orders. A guardrail metric, such as refunds, would show whether the win costs something elsewhere.
- **The simulation's timing hides a real limitation.** All 40,000 users arrived within 21 seconds, and each purchase came 1 ms after the exposure, so every user's 7-day window was effectively complete. In a real test, users exposed recently have had less time to buy, so every variant's rate reads low while the test runs (the comparison stays fair; [ADR-016](decisions.md#adr-016-how-events-are-attributed-and-the-limits-we-accept-m7)).
