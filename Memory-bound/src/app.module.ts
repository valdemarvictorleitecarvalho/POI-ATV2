import { Module } from '@nestjs/common';
import { AppController } from './app.controller';
import { MemoryService } from './memory.service';

@Module({
  controllers: [AppController],
  providers: [MemoryService],
})
export class AppModule {}
