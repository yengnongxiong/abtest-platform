/**
 * The new-experiment form's variant list, as a pure function the tests can reach.
 */

/** The control's position once the variant at `removed` is gone: it stays with its variant,
 * or, when the control itself was removed, the first variant takes over. */
export function controlAfterRemoving(control: number, removed: number): number {
  if (removed === control) {
    return 0;
  }
  return removed < control ? control - 1 : control;
}
