import {
  BadRequestException,
  Controller,
  Get,
  Query,
} from '@nestjs/common';
import {
  ComputeService,
  DEFAULT_LIMIT,
  MAX_LIMIT,
  MIN_LIMIT,
} from './compute.service';

@Controller()
export class AppController {
  constructor(private readonly computeService: ComputeService) {}

  @Get()
  info() {
    return {
      name: 'cpu-bound-backend',
      profile: 'cpu-bound',
      description:
        'Cada chamada a /compute conta primos por divisão tentativa no processo que a atendeu. O trabalho é de CPU, com memória extra constante por requisição.',
      endpoints: {
        health: 'GET /health',
        compute: 'GET /compute?limit=1000000',
      },
      limit: {
        default: DEFAULT_LIMIT,
        min: MIN_LIMIT,
        max: MAX_LIMIT,
      },
      pid: process.pid,
    };
  }

  @Get('health')
  health() {
    return { status: 'ok', profile: 'cpu-bound', pid: process.pid };
  }

  @Get('compute')
  compute(@Query('limit') limitParam?: string) {
    const limit =
      limitParam === undefined || limitParam === ''
        ? DEFAULT_LIMIT
        : Number(limitParam);

    if (!Number.isInteger(limit) || limit < MIN_LIMIT || limit > MAX_LIMIT) {
      throw new BadRequestException(
        `limit deve ser um inteiro entre ${MIN_LIMIT} e ${MAX_LIMIT}`,
      );
    }

    return this.computeService.count(limit);
  }
}
