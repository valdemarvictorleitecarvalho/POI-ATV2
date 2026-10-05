import { Injectable } from '@nestjs/common';

export const DEFAULT_LIMIT = 1_000_000;
export const MIN_LIMIT = 1_000;
export const MAX_LIMIT = 5_000_000;

/**
 * Contagem de primos por divisão tentativa.
 * A memória extra é constante; o tempo cresce com o limite.
 * Um crivo gastaria um vetor grande e deslocaria o perfil para memória.
 */
export function countPrimes(limit: number): number {
  if (limit < 2) {
    return 0;
  }

  let count = 1;
  for (let n = 3; n <= limit; n += 2) {
    if (isPrime(n)) {
      count++;
    }
  }
  return count;
}

function isPrime(n: number): boolean {
  const max = Math.floor(Math.sqrt(n));
  for (let d = 3; d <= max; d += 2) {
    if (n % d === 0) {
      return false;
    }
  }
  return true;
}

@Injectable()
export class ComputeService {
  count(limit: number) {
    const start = process.hrtime.bigint();
    const primeCount = countPrimes(limit);
    const elapsedMs = Number(process.hrtime.bigint() - start) / 1e6;

    return {
      profile: 'cpu-bound',
      operation: 'prime-count-trial-division',
      limit,
      primeCount,
      elapsedMs: Math.round(elapsedMs * 100) / 100,
      pid: process.pid,
    };
  }
}
