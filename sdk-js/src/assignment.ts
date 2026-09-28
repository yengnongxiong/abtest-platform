/**
 * Deterministic assignment, byte-identical to server/src/abtest/assignment.py (PRD §9).
 *
 * Both implementations are tested against shared/hash_test_vectors.json. Pure functions:
 * no I/O, no state.
 */

export const BUCKETS = 10_000; // basis points

export interface WeightedVariant {
  key: string;
  weight_bp: number;
  position: number;
}

const encoder = new TextEncoder();

/**
 * MurmurHash3 x86_32 of the string's UTF-8 bytes, seed 0, as an unsigned 32-bit integer.
 * `Math.imul` does 32-bit multiplication, and `>>> 0` reads the result as unsigned, which
 * is what Python's mmh3.hash(..., signed=False) returns.
 */
export function hash32(text: string): number {
  const bytes = encoder.encode(text);
  const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  const c1 = 0xcc9e2d51;
  const c2 = 0x1b873593;
  const blocksEnd = bytes.length - (bytes.length % 4);
  let h = 0; // seed 0

  for (let i = 0; i < blocksEnd; i += 4) {
    let k = view.getUint32(i, true); // little-endian, as the reference reads blocks
    k = Math.imul(k, c1);
    k = (k << 15) | (k >>> 17);
    k = Math.imul(k, c2);
    h ^= k;
    h = (h << 13) | (h >>> 19);
    h = (Math.imul(h, 5) + 0xe6546b64) | 0;
  }

  // The 1-3 bytes after the last full block.
  const tail = bytes.length % 4;
  if (tail > 0) {
    let k = 0;
    for (let i = tail - 1; i >= 0; i--) {
      k = (k << 8) | (bytes[blocksEnd + i] ?? 0);
    }
    k = Math.imul(k, c1);
    k = (k << 15) | (k >>> 17);
    k = Math.imul(k, c2);
    h ^= k;
  }

  // Finalization: mix the bits so similar inputs land far apart.
  h ^= bytes.length;
  h ^= h >>> 16;
  h = Math.imul(h, 0x85ebca6b);
  h ^= h >>> 13;
  h = Math.imul(h, 0xc2b2ae35);
  h ^= h >>> 16;
  return h >>> 0;
}

export function bucket(text: string): number {
  return hash32(text) % BUCKETS;
}

export function inExperiment(experimentKey: string, userId: string, trafficBp: number): boolean {
  return bucket(`${experimentKey}:traffic:${userId}`) < trafficBp;
}

/**
 * Walk the variants by position, adding up weights; the user's bucket picks the first
 * variant whose running total exceeds it. Weights must sum to 10,000.
 */
export function chooseVariant(
  experimentKey: string,
  userId: string,
  variants: readonly WeightedVariant[],
): string {
  const position = bucket(`${experimentKey}:variant:${userId}`);
  let cumulative = 0;
  for (const variant of [...variants].sort((a, b) => a.position - b.position)) {
    cumulative += variant.weight_bp;
    if (position < cumulative) {
      return variant.key;
    }
  }
  throw new Error(`variant weights sum to ${String(cumulative)}, not ${String(BUCKETS)}`);
}

/** The variant a user sees, or null if the user isn't in the experiment's traffic. */
export function assign(
  experimentKey: string,
  userId: string,
  trafficBp: number,
  variants: readonly WeightedVariant[],
): string | null {
  if (!inExperiment(experimentKey, userId, trafficBp)) {
    return null;
  }
  return chooseVariant(experimentKey, userId, variants);
}

export function flagEnabled(
  flagKey: string,
  userId: string,
  enabled: boolean,
  rolloutBp: number,
): boolean {
  return enabled && bucket(`${flagKey}:rollout:${userId}`) < rolloutBp;
}

/**
 * True if the id has a lone UTF-16 surrogate. TextEncoder would silently replace it with
 * U+FFFD while Python refuses to encode it, so the two hashes would disagree; the SDK
 * refuses such ids instead (the API rejects them too).
 */
export function hasLoneSurrogate(text: string): boolean {
  return /[\uD800-\uDBFF](?![\uDC00-\uDFFF])|(?<![\uD800-\uDBFF])[\uDC00-\uDFFF]/.test(text);
}
