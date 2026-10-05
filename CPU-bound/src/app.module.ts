import { Module } from '@nestjs/common';
import { AppController } from './app.controller';
import { ComputeService } from './compute.service';

@Module({
  controllers: [AppController],
  providers: [ComputeService],
})
export class AppModule {}
