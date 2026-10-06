import {
  BadRequestException,
  Controller,
  Get,
  Query,
} from '@nestjs/common';
import {
  DATA_DIR,
  DEFAULT_SIZE_KB,
  DEFAULT_SYNCS,
  IoService,
  MAX_SIZE_KB,
  MAX_SYNCS,
  MIN_SIZE_KB,
  MIN_SYNCS,
} from './io.service';

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
  constructor(private readonly ioService: IoService) {}

  @Get()
  info() {
    return {
      name: 'io-bound-backend',
      profile: 'io-bound',
      description:
        'Cada chamada a /io grava um arquivo em disco em blocos, com fsync após cada bloco, e o apaga em seguida. O tempo é dominado pela espera do disco.',
      endpoints: {
        health: 'GET /health',
        io: 'GET /io?sizeKb=256&syncs=4',
      },
      sizeKb: { default: DEFAULT_SIZE_KB, min: MIN_SIZE_KB, max: MAX_SIZE_KB },
      syncs: { default: DEFAULT_SYNCS, min: MIN_SYNCS, max: MAX_SYNCS },
      dataDir: DATA_DIR,
      pid: process.pid,
    };
  }

  @Get('health')
  health() {
    return { status: 'ok', profile: 'io-bound', pid: process.pid };
  }

  @Get('io')
  io(@Query('sizeKb') sizeKb?: string, @Query('syncs') syncs?: string) {
    return this.ioService.write(
      parseIntParam('sizeKb', sizeKb, DEFAULT_SIZE_KB, MIN_SIZE_KB, MAX_SIZE_KB),
      parseIntParam('syncs', syncs, DEFAULT_SYNCS, MIN_SYNCS, MAX_SYNCS),
    );
  }
}
