import { BadRequestException, Controller, Get, Query } from '@nestjs/common';
import {
  DEFAULT_HOLD_MS,
  DEFAULT_PASSES,
  DEFAULT_SIZE_MB,
  MAX_HOLD_MS,
  MAX_INFLIGHT_MB,
  MAX_PASSES,
  MAX_SIZE_MB,
  MemoryService,
  MIN_HOLD_MS,
  MIN_PASSES,
  MIN_SIZE_MB,
} from './memory.service';

function parseIntParam(
  name: string,
  raw: string | undefined,
  fallback: number,
  min: number,
  max: number,
): number {
  const value = raw === undefined || raw === '' ? fallback : Number(raw);
  if (!Number.isInteger(value) || value < min || value > max) {
    throw new BadRequestException(
      `${name} deve ser um inteiro entre ${min} e ${max}`,
    );
  }
  return value;
}

@Controller()
export class AppController {
  constructor(private readonly memoryService: MemoryService) {}

  @Get()
  info() {
    return {
      name: 'memory-bound-backend',
      profile: 'memory-bound',
      description:
        'Cada chamada a /memory aloca um bloco de RAM, escreve em todas as páginas, o retém por um tempo e faz acessos aleatórios a ele. A memória em uso cresce com as requisições em voo; a CPU fica esperando a RAM.',
      endpoints: {
        health: 'GET /health',
        stats: 'GET /stats',
        memory: 'GET /memory?sizeMb=64&holdMs=500&passes=1',
      },
      sizeMb: { default: DEFAULT_SIZE_MB, min: MIN_SIZE_MB, max: MAX_SIZE_MB },
      holdMs: { default: DEFAULT_HOLD_MS, min: MIN_HOLD_MS, max: MAX_HOLD_MS },
      passes: { default: DEFAULT_PASSES, min: MIN_PASSES, max: MAX_PASSES },
      maxInflightMb: MAX_INFLIGHT_MB,
      pid: process.pid,
    };
  }

  @Get('health')
  health() {
    return { status: 'ok', profile: 'memory-bound', pid: process.pid };
  }

  @Get('stats')
  stats() {
    return this.memoryService.stats();
  }

  @Get('memory')
  memory(
    @Query('sizeMb') sizeMb?: string,
    @Query('holdMs') holdMs?: string,
    @Query('passes') passes?: string,
  ) {
    return this.memoryService.touch(
      parseIntParam('sizeMb', sizeMb, DEFAULT_SIZE_MB, MIN_SIZE_MB, MAX_SIZE_MB),
      parseIntParam('holdMs', holdMs, DEFAULT_HOLD_MS, MIN_HOLD_MS, MAX_HOLD_MS),
      parseIntParam('passes', passes, DEFAULT_PASSES, MIN_PASSES, MAX_PASSES),
    );
  }
}
