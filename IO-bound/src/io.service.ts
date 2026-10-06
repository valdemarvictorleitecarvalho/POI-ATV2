import { Injectable } from '@nestjs/common';
import { randomBytes, randomUUID } from 'node:crypto';
import { mkdirSync } from 'node:fs';
import { open, unlink } from 'node:fs/promises';
import { join, resolve } from 'node:path';

export const DEFAULT_SIZE_KB = 256;
export const MIN_SIZE_KB = 4;
export const MAX_SIZE_KB = 8192;
export const DEFAULT_SYNCS = 4;
export const MIN_SYNCS = 1;
export const MAX_SYNCS = 64;

export const DATA_DIR = resolve(process.env.DATA_DIR ?? 'data');

const payload = randomBytes(MAX_SIZE_KB * 1024);

const msSince = (start: bigint) =>
  Math.round((Number(process.hrtime.bigint() - start) / 1e6) * 100) / 100;

export function prepareDataDir(): void {
  mkdirSync(DATA_DIR, { recursive: true });
}

export async function writeDurably(sizeKb: number, syncs: number) {
  const total = sizeKb * 1024;
  const chunk = Math.ceil(total / syncs);
  const path = join(DATA_DIR, `${process.pid}-${randomUUID()}.bin`);
  const start = process.hrtime.bigint();
  let syncNs = 0n;

  const file = await open(path, 'w');
  try {
    for (let offset = 0; offset < total; offset += chunk) {
      const end = Math.min(offset + chunk, total);
      await file.write(payload, offset, end - offset, offset);
      const syncStart = process.hrtime.bigint();
      await file.sync();
      syncNs += process.hrtime.bigint() - syncStart;
    }
  } finally {
    await file.close();
    await unlink(path);
  }

  return {
    bytesWritten: total,
    syncs,
    elapsedMs: msSince(start),
    syncMs: Math.round((Number(syncNs) / 1e6) * 100) / 100,
  };
}

@Injectable()
export class IoService {
  async write(sizeKb: number, syncs: number) {
    return {
      profile: 'io-bound',
      operation: 'write-fsync',
      sizeKb,
      ...(await writeDurably(sizeKb, syncs)),
      pid: process.pid,
    };
  }
}
