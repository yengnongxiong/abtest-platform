# scenarios

Inputs for the traffic generator (`make traffic SCENARIO=<name>`), which sends seeded, simulated users through the running stack and prints the results next to the true effect. Each file sets the experiment, each variant's true conversion rate, the number of users, and an optional logging bug.

Read first:
- [checkout_button.yaml](checkout_button.yaml): a bigger Buy button with a true +8% lift
- [srm_bug.yaml](srm_bug.yaml): the same test, but 5% of one variant's exposures are lost, which the sample ratio check must flag
