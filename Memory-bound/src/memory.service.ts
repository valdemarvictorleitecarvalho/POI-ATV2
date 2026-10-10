import { Injectable, ServiceUnavailableException } from '@nestjs/common';
import { freemem, totalmem } from 'node:os';

export const DEFAULT_SIZE_MB = 64;
export const MIN_SIZE_MB = 1;
export const MAX_SIZE_MB = 512;
export const DEFAULT_HOLD_MS = 500;
export const MIN_HOLD_MS = 0;
export const MAX_HOLD_MS = 10_000;
export const DEFAULT_PASSES = 1;
export const MIN_PASSES = 1;
export const MAX_PASSES = 16;

/**
 * Teto de memória em requisições simultâneas, por processo (0 = sem teto).
 * Serve para proteger a VM de ser morta pelo OOM killer em testes agressivos.
 */
export const MAX_INFLIGHT_MB = Number(process.env.MAX_INFLIGHT_MB ?? 0);

const BYTES_PER_MB = 1024 * 1024;
const WORDS_PER_CACHE_LINE = 16; // 16 palavras de 4 bytes = 64 bytes

/**
 * Libera o bloco na hora. Sem isso a RAM só volta quando o GC decide rodar, e
 * a memória residente passaria a refletir o GC, não as requisições em voo.
 * transfer(0) desanexa o ArrayBuffer e devolve a memória ao sistema.
 */
function release(view: Uint32Array): void {
  const buffer = view.buffer as ArrayBuffer & {
    transfer?: (newByteLength?: number) => ArrayBuffer;
  };
  buffer.transfer?.(0);
}

const round2 = (value: number) => Math.round(value * 100) / 100;
const msSince = (start: bigint) =>
  round2(Number(process.hrtime.bigint() - start) / 1e6);
const toMb = (bytes: number) => Math.round((bytes / BYTES_PER_MB) * 10) / 10;

/**
 * Acessos pseudoaleatórios (xorshift32) ao bloco, um por linha de cache por
 * passada. Cada acesso cai numa posição imprevisível, então o prefetcher não
 * ajuda e a CPU passa o tempo esperando a RAM. Também escreve, para as
 * páginas continuarem sujas e residentes.
 */
export function randomAccess(
  view: Uint32Array,
  passes: number,
  seed: number,
): number {
  const accesses = Math.floor(view.length / WORDS_PER_CACHE_LINE) * passes;
  const length = view.length;
  let x = seed >>> 0 || 2463534242;
  let checksum = 0;

  for (let i = 0; i < accesses; i++) {
    x ^= x << 13;
    x ^= x >>> 17;
    x ^= x << 5;
    const index = (x >>> 0) % length;
    view[index] += x;
    checksum ^= view[index];
  }
  return checksum >>> 0;
}

@Injectable()
export class MemoryService {
  private inflightMb = 0;
  private served = 0;

  async touch(sizeMb: number, holdMs: number, passes: number) {
    if (MAX_INFLIGHT_MB > 0 && this.inflightMb + sizeMb > MAX_INFLIGHT_MB) {
      throw new ServiceUnavailableException(
        `processo ${process.pid} no teto de ${MAX_INFLIGHT_MB} MB em voo`,
      );
    }

    const inflightAtStart = this.inflightMb;
    this.inflightMb += sizeMb;
    const start = process.hrtime.bigint();

    let view: Uint32Array | undefined;
    try {
      // fill() escreve em todas as páginas, forçando a RAM a ser realmente
      // usada (um Uint32Array recém-criado é só memória virtual zerada).
      view = new Uint32Array((sizeMb * BYTES_PER_MB) / 4);
      view.fill(0x9e3779b9);
      const allocMs = msSince(start);

      // O bloco fica retido enquanto a requisição espera. É isso que faz a
      // memória residente crescer com a quantidade de requisições em voo.
      const holdStart = process.hrtime.bigint();
      await new Promise((resolve) => setTimeout(resolve, holdMs));
      const heldMs = msSince(holdStart);

      const accessStart = process.hrtime.bigint();
      const checksum = randomAccess(view, passes, Date.now() ^ process.pid);
      const accessMs = msSince(accessStart);

      this.served++;
      return {
        profile: 'memory-bound',
        operation: 'allocate-hold-random-access',
        sizeMb,
        holdMs,
        passes,
        checksum,
        allocMs,
        heldMs,
        accessMs,
        elapsedMs: msSince(start),
        inflightAtStartMb: inflightAtStart,
        rssMb: toMb(process.memoryUsage().rss),
        pid: process.pid,
      };
    } finally {
      if (view) {
        release(view);
      }
      this.inflightMb -= sizeMb;
    }
  }

  stats() {
    const usage = process.memoryUsage();
    return {
      profile: 'memory-bound',
      pid: process.pid,
      served: this.served,
      inflightMb: this.inflightMb,
      maxInflightMb: MAX_INFLIGHT_MB,
      rssMb: toMb(usage.rss),
      heapUsedMb: toMb(usage.heapUsed),
      arrayBuffersMb: toMb(usage.arrayBuffers),
      systemFreeMb: toMb(freemem()),
      systemTotalMb: toMb(totalmem()),
    };
  }
}
