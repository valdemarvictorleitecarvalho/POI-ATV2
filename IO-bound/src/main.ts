import 'reflect-metadata';
import { NestFactory } from '@nestjs/core';
import cluster from 'node:cluster';
import { availableParallelism } from 'node:os';
import { AppModule } from './app.module';
import { DATA_DIR, prepareDataDir } from './io.service';

function workerCount(): number {
  const configured = Number(process.env.WEB_CONCURRENCY);
  if (Number.isInteger(configured) && configured > 0) {
    return configured;
  }
  return Math.max(1, availableParallelism());
}

async function bootstrap(): Promise<void> {
  const app = await NestFactory.create(AppModule);
  app.enableShutdownHooks();
  const port = Number(process.env.PORT ?? 3000);
  await app.listen(port, '0.0.0.0');
}

const clusteringEnabled = process.env.CLUSTER !== 'false';

if (clusteringEnabled && cluster.isPrimary) {
  prepareDataDir();
  const count = workerCount();
  console.log(
    `Processo principal ${process.pid} iniciando ${count} worker(s). Dados em ${DATA_DIR}.`,
  );
  for (let i = 0; i < count; i++) {
    cluster.fork();
  }
} else {
  prepareDataDir();
  void bootstrap();
}
